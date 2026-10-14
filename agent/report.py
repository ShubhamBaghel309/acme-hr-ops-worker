"""The final report, assembled from verified state. No free-form LLM summary, so nothing to hallucinate.

The only model-written text is labelled as such: the verifier's note on a failed criterion,
and the worker's reason when it stops.
"""
from agent.prompts import fill_placeholders

HEADLINES = {"done": "Done", "stopped": "Needs you", "done_with_failures": "Not done",
             "budget_exhausted": "Not done", "running": "In progress"}
NOTABLE = {"auth_expired", "server_error", "page_error", "rejected", "stale_ref", "blocked", "error",
           "not_found", "verify_failed", "needs_approval"}


def build_report(state: dict) -> dict:
    spec, facts, steps = state.get("spec") or {}, state.get("facts", {}), state.get("steps", [])
    verdicts = state.get("verdicts", {})
    status = state.get("status", "running")
    stop = next((s for s in reversed(steps) if s["tool"] == "finish" and s["outcome"] == "stopped"), None)
    return {
        "headline": HEADLINES.get(status, status),
        "status": status,
        "request": state.get("request", ""),
        "sop": spec.get("sop"),
        "criteria": [{"id": c["id"], "text": fill_placeholders(c["text"], facts),
                      **verdicts.get(c["id"], {"status": "not checked"})} for c in spec.get("criteria", [])],
        "answer": [{"key": k, **facts[k]} for k in state.get("answer_facts", []) if k in facts],
        "facts": [{"key": k, **f} for k, f in facts.items()],
        "human": [s["args"] for s in steps if s["tool"] == "human"],
        "recoveries": [{"step": s["n"], "tool": s["tool"], "outcome": s["outcome"], "message": s["message"]}
                       for s in steps if s["outcome"] in NOTABLE or "signed in again" in s["message"]],
        "stopped_because": stop["why"] if stop else None,
        "steps": {"work": sum(s["mode"] == "work" for s in steps), "verify": sum(s["mode"] == "verify" for s in steps)},
        "tokens": {k: state.get("counters", {}).get(k, 0) for k in ("tokens_in", "tokens_out")},
    }


def to_markdown(report: dict) -> str:
    mark = {"pass": "PASS", "fail": "FAIL", "not checked": "----"}
    lines = [f"# {report['headline']}", f"Request: {report['request']}", f"Procedure: {report['sop']}", ""]
    if report["stopped_because"]:
        lines += [f"Stopped. Worker's reason (model-written): {report['stopped_because']}", ""]
    if report["answer"]:
        lines += ["## Answer"] + [f"- {a['key']}: {a['raw']}  (\"{a['quote']}\", {a['source']})" for a in report["answer"]] + [""]
    lines.append("## Success criteria (independently verified)")
    for c in report["criteria"]:
        lines.append(f"- [{mark.get(c['status'], c['status'])}] {c['id']}: {c['text']}")
        for q in c.get("quotes") or []:
            lines.append(f"    evidence on {c.get('url')}: \"{q}\"")
        if c["status"] == "fail" and c.get("note"):
            lines.append(f"    verifier's note (model-written): {c['note']}")
    lines += ["", "## Facts used (each backed by a quote)"]
    lines += [f"- {f['key']} = {f['raw']}  (\"{f['quote']}\", {f['source']})" for f in report["facts"]]
    if report["human"]:
        lines += ["", "## Human decisions"]
        for h in report["human"]:
            if "decision" in h:
                lines.append(f"- {h['description']}: {h['decision']} {h.get('payload')}")
            else:
                lines.append(f"- Q: {h['question']}\n  A: {h['answer']}")
    if report["recoveries"]:
        lines += ["", "## Problems handled"]
        lines += [f"- step {r['step']} {r['tool']}: {r['outcome']} - {r['message']}" for r in report["recoveries"]]
    lines += ["", f"Steps: {report['steps']['work']} work, {report['steps']['verify']} verify. "
                  f"Tokens: {report['tokens']['tokens_in']} in, {report['tokens']['tokens_out']} out."]
    if report.get("trace_url"):
        lines.append(f"Trace: {report['trace_url']}")
    return "\n".join(lines)
