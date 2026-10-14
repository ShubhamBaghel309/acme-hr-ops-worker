"""The tools the LLM can call, and their deterministic execution.

The LLM proposes one call per step. `run_tool` validates it (current refs, grounding),
executes it, observes the page, classifies the outcome and recovers where that is safe,
so the next step can adapt. Nothing here knows about any particular company or app.
"""
import json
import re
from io import BytesIO
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from pypdf import PdfReader

from agent import grounding
from agent.browser import Browser, Observation
from agent.policy import Company

FAILURES = {"rejected", "page_error", "server_error", "stale_ref", "not_found", "blocked", "error", "auth_expired"}


def _tool(name: str, description: str, props: dict) -> dict:
    why = {"type": "string", "description": "One short sentence: why this action, now."}
    return {
        "type": "function", "name": name, "description": description, "strict": True,
        "parameters": {"type": "object", "properties": {**props, "why": why},
                       "required": [*props, "why"], "additionalProperties": False},
    }


STR = {"type": "string"}
REF = {"type": "string", "description": "An element ref such as e12 from the CURRENT page snapshot."}
SOURCE = {"type": "string", "description": "Where the value comes from: fact:<key>, request, answer:<n>, "
                                           "policy:<doc>, or composed (free text whose emails, dates and numbers all come from facts)."}

GOTO = _tool("goto", "Open a company app by its id, or any portal path starting with / (query strings allowed).",
             {"where": STR})
CLICK = _tool("click", "Click a link, button, checkbox or radio button.", {"ref": REF})
FILL = _tool("fill", "Type into a text field or text area (replaces what is there).",
             {"ref": REF, "value": STR, "source": SOURCE})
SELECT = _tool("select", "Choose a dropdown option. `option` may be a distinctive part of the option text, such as an ID.",
               {"ref": REF, "option": STR, "source": SOURCE})
OPEN = _tool("open_attachment", "Open a linked document (such as a PDF attachment) and read its text.", {"ref": REF})
RECORD = _tool("record_fact", "Remember a fact you found, with the exact quote that proves it.", {
    "key": {"type": "string", "description": "Use the SOP's required-fact key when there is one."},
    "value": STR,
    "type": {"type": "string", "enum": list(grounding.FACT_TYPES)},
    "quote": {"type": "string", "description": "Exact text copied from the source that contains the value."},
    "source_id": {"type": "string", "description": "page (the current page), request, doc:<n>, answer:<n>, "
                                                    "or policy:<doc> (a company document)."},
})
ASK = _tool("ask_human", "Ask a human when something is missing, ambiguous, risky or outside the SOP. Waits for the answer.",
            {"question": STR})
FINISH = _tool("finish", "End the work. completed: every plan step is done (an independent check follows). "
                         "stopped: you cannot or should not continue.", {
    "outcome": {"type": "string", "enum": ["completed", "stopped"]},
    "answer_facts": {"type": "array", "items": STR, "description": "For a question: the fact keys that answer it. Otherwise []."},
})
VERDICT = _tool("verdict", "Record your verdict on one success criterion.", {
    "criterion_id": STR,
    "status": {"type": "string", "enum": ["pass", "fail"]},
    "quotes": {"type": "array", "items": STR,
               "description": "For pass: exact texts, copied from pages you viewed while verifying, that together prove the criterion."},
})

WORK_TOOLS = [GOTO, CLICK, FILL, SELECT, OPEN, RECORD, ASK, FINISH]
VERIFY_TOOLS = [GOTO, CLICK, FILL, OPEN, VERDICT]


def _page(state: dict) -> str:
    return state.get("page_text", "") + "\n" + state.get("obs", "")


def sources(state: dict, company: Company) -> dict[str, str]:
    """Everything a fact or a write may cite."""
    # "page" covers both the visible text and the snapshot the agent reads (tables appear as | a | b |)
    out = {"request": state["request"], "page": _page(state)}
    out.update({doc_id: doc["text"] for doc_id, doc in state.get("docs", {}).items()})
    out.update({f"answer:{i}": a for i, a in enumerate(state.get("answers", []), start=1)})
    out.update(company.docs)
    return out


def _day_first(company: Company) -> bool:
    return company.profile.get("date_locale", "DMY") == "DMY"


def _ref_line(state: dict, ref: str) -> str | None:
    return next((line for line in state.get("obs", "").splitlines() if f"[{ref}]" in line), None)


async def _settle(browser: Browser, obs: Observation) -> tuple[str, str, Observation]:
    """Classify what an action did, and recover where that is safe."""
    if held := browser.held:
        if held.action == "approve":
            return "needs_approval", f"Held for human approval: {held.description}.", obs
        return "blocked", f"Blocked by company policy: {held.description}. Change the action; don't retry it as is.", obs
    if browser.on_login_page():
        await browser.login()
        fresh = await browser.observe()
        if obs.posted:
            return ("auth_expired", "Your session had expired, so the form you submitted was NOT saved. "
                    "You are signed in again; fill the form again and resubmit.", fresh)
        return "ok", "Your session had expired; you were signed in again automatically.", fresh
    if obs.status and obs.status >= 500:
        if obs.posted:
            return ("server_error", f"The server returned an error (HTTP {obs.status}) after you submitted. "
                    "The change may or may not have been saved: check (e.g. search for the record) before submitting again.", obs)
        return "server_error", f"The server returned an error (HTTP {obs.status}). Try again.", obs
    if obs.status == 404:
        return "not_found", "That page does not exist (HTTP 404).", obs
    alerts = [line.removeprefix("[alert] ") for line in obs.snapshot.splitlines() if line.startswith("[alert]")]
    if alerts:
        return "page_error", "The page shows an error: " + " | ".join(alerts), obs
    return "ok", "", obs


async def _goto(args, state, browser, company):
    where = args["where"].strip()
    apps = {app["id"]: app["path"] for app in company.profile["apps"]}
    path = apps.get(where.lower(), where)
    if not path.startswith("/"):
        return "rejected", f"Use an app id ({', '.join(apps)}) or a path starting with /.", None, {}
    obs = await browser.goto(path)
    if obs.status and obs.status >= 500 and not obs.posted:  # a failed page load is safe to retry once
        obs = await browser.goto(path)
    return *(await _settle(browser, obs)), {}


async def _click(args, state, browser, company):
    return *(await _settle(browser, await browser.click(args["ref"]))), {}


async def _fill(args, state, browser, company):
    line = _ref_line(state, args["ref"])
    if not re.search(r"\] (textbox|textarea) ", line):
        return "rejected", f"{args['ref']} is not a text field.", None, {}
    if msg := grounding.check_write(args["value"], args["source"], state.get("facts", {}), sources(state, company),
                                    _day_first(company)):
        return "rejected", msg, None, {}
    return *(await _settle(browser, await browser.fill(args["ref"], args["value"]))), {}


async def _select(args, state, browser, company):
    line = _ref_line(state, args["ref"])
    m = re.search(r"\] select .* options=(\[.*\])$", line)
    if not m:
        return "rejected", f"{args['ref']} is not a dropdown.", None, {}
    if msg := grounding.check_write(args["option"], args["source"], state.get("facts", {}), sources(state, company),
                                    _day_first(company)):
        return "rejected", msg, None, {}
    options = json.loads(m.group(1))
    matches = grounding.match_options(args["option"], options)
    if len(matches) != 1:
        if not matches:
            return "rejected", f"No option matches {args['option']!r}. Options: {options}", None, {}
        return ("rejected", f"{len(matches)} options match {args['option']!r}: {matches}. Pick using something that "
                "identifies exactly one (such as an ID) from your evidence, or ask_human if your evidence can't tell them apart.",
                None, {})
    return *(await _settle(browser, await browser.select(args["ref"], matches[0]))), {}


async def _open_attachment(args, state, browser, company):
    line = _ref_line(state, args["ref"])
    m = re.search(r'\] link "(.*)" -> (\S+)$', line)
    if not m:
        return "rejected", f"{args['ref']} is not a link.", None, {}
    for doc_id, doc in state.get("docs", {}).items():
        if doc["name"] == m.group(1):
            return "ok", f"{m.group(1)} is already open as {doc_id}; its text is under OPENED DOCUMENTS.", None, {}
    data, decision = await browser.fetch(m.group(2))
    if data is None:
        return "blocked", f"Blocked by company policy: {decision.description}.", None, {}
    if data[:4] == b"%PDF":
        text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)
    else:
        text = data.decode("utf-8", "replace")
    doc_id = f"doc:{len(state.get('docs', {})) + 1}"
    message = f"Opened {m.group(1)} as {doc_id} ({len(text.splitlines())} lines). Its text is under OPENED DOCUMENTS."
    return "ok", message, None, {"docs": {doc_id: {"name": m.group(1), "text": text}}}


async def _record_fact(args, state, browser, company):
    value, msg = grounding.check_fact(args["value"], args["type"], args["quote"], args["source_id"],
                                      sources(state, company), _day_first(company))
    if msg:
        return "rejected", msg, None, {}
    fact = {"value": value, "raw": args["value"], "type": args["type"], "source": args["source_id"], "quote": args["quote"]}
    return "ok", f"Recorded {args['key']} = {args['value']!r}.", None, {"facts": {args["key"]: fact}}


async def _ask_human(args, state, browser, company):
    return "asked", "Waiting for a human answer.", None, {"pending": {"kind": "question", "question": args["question"]}}


async def _finish(args, state, browser, company):
    if args["outcome"] == "stopped":
        return "stopped", "Stopped without completing the task.", None, {"status": "stopped"}
    missing = [k for k in args["answer_facts"] if k not in state.get("facts", {})]
    if missing:
        return "rejected", f"Unknown fact keys in answer_facts: {missing}. Record them first.", None, {}
    browser.mode = "verify"
    counters = {**state["counters"], "verify": 0}
    return ("finished", "Work is finished; an independent verification starts now.", None,
            {"mode": "verify", "verdicts": {}, "seen": {}, "answer_facts": args["answer_facts"], "repair_focus": [],
             "counters": counters})


async def _verdict(args, state, browser, company, why: str = "", screenshot: str | None = None):
    criteria = {c["id"] for c in state["spec"]["criteria"]}
    cid = args["criterion_id"]
    if cid not in criteria:
        return "rejected", f"Unknown criterion {cid!r}. Criteria: {sorted(criteria)}.", None, {}
    # Evidence may come from any page viewed during this verification round (a criterion can span pages).
    seen = {**state.get("seen", {}), state.get("url", ""): {"snapshot": state.get("obs", ""), "text": state.get("page_text", "")}}
    evidence_urls = []
    if args["status"] == "pass":
        if not args["quotes"]:
            return "rejected", "A pass needs at least one quote from a page you viewed during verification.", None, {}
        for quote in args["quotes"]:
            urls = [url for url, page in seen.items()
                    if not grounding.check_claim(quote, page["text"] + "\n" + page["snapshot"])]
            if not urls:
                return "rejected", (f"{grounding.check_claim(quote, _page(state))} (quote: {quote!r}). "
                                    "Quotes must be copied from pages you viewed during this verification."), None, {}
            evidence_urls.append(urls[0])
    verdicts = {**state.get("verdicts", {}), cid: {
        "status": args["status"], "quotes": args["quotes"], "url": ", ".join(dict.fromkeys(evidence_urls)) or state.get("url"),
        "note": why, "screenshot": screenshot}}
    updates = {"verdicts": verdicts}
    if criteria - set(verdicts):
        return "ok", f"Recorded {cid}: {args['status']}. Still to check: {sorted(criteria - set(verdicts))}.", None, updates
    failed = sorted(c for c, v in verdicts.items() if v["status"] == "fail")
    if not failed:
        return "ok", "All criteria verified.", None, {**updates, "status": "done"}
    counters = dict(state["counters"])
    if counters["repair"] < 1:
        counters["repair"] += 1
        counters["work"] = 0  # the repair round gets its own step budget
        browser.mode = "work"
        notes = "; ".join(f"{c}: {verdicts[c]['note']}" for c in failed)
        return ("verify_failed", f"Verification failed for {failed} ({notes}). Back to work to fix them.", None,
                {**updates, "mode": "work", "repair_focus": failed, "counters": counters})
    return "verify_failed", f"Verification failed for {failed}.", None, {**updates, "status": "done_with_failures"}


async def _screenshot(browser: Browser, run_dir: Path, n: int) -> str:
    """Save the current page as evidence; returns the path relative to the runs folder."""
    run_dir.mkdir(parents=True, exist_ok=True)
    await browser.screenshot(str(run_dir / f"step_{n:03d}.png"))
    return f"{run_dir.name}/step_{n:03d}.png"


HANDLERS = {"goto": _goto, "click": _click, "fill": _fill, "select": _select, "open_attachment": _open_attachment,
            "record_fact": _record_fact, "ask_human": _ask_human, "finish": _finish}
NEEDS_REF = {"click", "fill", "select", "open_attachment"}


async def run_tool(call: dict, state: dict, browser: Browser, company: Company, run_dir: Path) -> dict:
    """Execute one proposed tool call; return the graph state updates, always including one step record."""
    tool, args = call["tool"], {k: v for k, v in call["args"].items() if k != "why"}
    why, mode, steps = call["args"].get("why", ""), state["mode"], state.get("steps", [])
    n = len(steps) + 1
    allowed = {t["name"] for t in (WORK_TOOLS if mode == "work" else VERIFY_TOOLS)}
    updates, obs, screenshot = {}, None, None
    if tool not in allowed:
        outcome, message = "rejected", f"{tool} is not available in {mode} mode. Use one of: {sorted(allowed)}."
    elif tool in NEEDS_REF and not _ref_line(state, args["ref"]):
        outcome, message = "stale_ref", f"{args['ref']} is not on the current page. Use a ref from the current snapshot."
    elif tool == "verdict":
        screenshot = await _screenshot(browser, run_dir, n)
        outcome, message, obs, updates = await _verdict(args, state, browser, company, why, screenshot)
    else:
        try:
            outcome, message, obs, updates = await HANDLERS[tool](args, state, browser, company)
        except PlaywrightError as e:
            outcome, message = "error", f"The browser action failed: {str(e).splitlines()[0]}. Look at the page and try again."
            obs = await browser.observe()

    if obs is not None and tool != "verdict":
        screenshot = await _screenshot(browser, run_dir, n)
        updates.update(obs=obs.snapshot, page_text=obs.page_text, url=obs.url)
        if mode == "verify":
            updates["seen"] = {**state.get("seen", {}), obs.url: {"snapshot": obs.snapshot, "text": obs.page_text}}

    previous = steps[-1] if steps else None
    if outcome in FAILURES and previous and previous["tool"] == tool and previous["args"] == args \
            and previous["outcome"] in FAILURES:
        message = "You already tried exactly this and it failed. Do something different. " + message

    counters = dict(updates.get("counters", state["counters"]))
    counters[mode] = counters.get(mode, 0) + 1
    counters["consec_fail"] = counters.get("consec_fail", 0) + 1 if outcome in FAILURES else 0
    if outcome == "needs_approval":
        held = browser.held
        updates["pending"] = {"kind": "approval", "rule": held.rule, "description": held.description,
                              "payload": held.payload, "key_hash": held.key_hash}
    elif counters["consec_fail"] >= 3 and "pending" not in updates:
        recent = [s["message"] for s in steps[-2:]] + [message]
        updates["pending"] = {"kind": "question", "question": "I'm stuck; my last three attempts failed:\n- "
                              + "\n- ".join(recent) + "\nHow should I proceed?"}
        counters["consec_fail"] = 0
    updates["counters"] = counters
    updates["steps"] = [{"n": n, "mode": mode, "tool": tool, "args": args, "why": why, "outcome": outcome,
                         "message": message, "url": updates.get("url", state.get("url")), "screenshot": screenshot}]
    return updates
