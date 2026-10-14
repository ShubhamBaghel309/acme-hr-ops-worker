"""The worker, as a LangGraph state machine.

    START -> understand -> act <-> execute -> report -> END
                  |         ^   |
                  +-> human-+---+   (approvals, questions, circuit breaker)

- understand: request + company documents -> spec (SOP, required facts, success criteria, plan).
- act: the LLM proposes exactly one tool call, from a prompt rebuilt from state at every step.
- execute: deterministic code runs it: refs, grounding, egress gate, outcome classification, recovery.
  `finish` flips the same act/execute loop into verify mode, where a separate prompt checks each
  criterion read-only. Failed criteria get one repair round.
- human: the only node that calls interrupt(), so approvals and answers survive restarts.
- report: templated from verified state.
"""
import json
import operator
import time
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI
from pydantic import BaseModel

from agent import prompts
from agent.browser import close_browser, get_browser
from agent.policy import load_company
from agent.report import build_report
from agent.tools import VERIFY_TOOLS, WORK_TOOLS, run_tool

COMPANY = load_company()
RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"
BUDGET = {"work": 80, "verify": 20}


# ---------- state ----------

def _merge(old: dict | None, new: dict | None) -> dict:
    return {**(old or {}), **(new or {})}


class State(TypedDict, total=False):
    request: str
    spec: dict
    facts: Annotated[dict, _merge]        # key -> {value, raw, type, source, quote}
    docs: Annotated[dict, _merge]         # doc:<n> -> {name, text}
    answers: Annotated[list, operator.add]
    decisions: Annotated[list, operator.add]  # human approval decisions
    steps: Annotated[list, operator.add]  # append-only log; the "why" of every action
    obs: str                              # current page snapshot (latest only)
    page_text: str
    url: str
    proposed: dict                        # the tool call act proposed; replayed after an approval
    pending: dict | None                  # what the human node will ask
    human_decision: str
    verdicts: dict
    seen: dict                            # url -> {snapshot, text} of pages viewed during verification
    answer_facts: list
    repair_focus: list
    mode: str                             # work | verify
    counters: dict
    status: str                           # running | done | done_with_failures | stopped | budget_exhausted
    report: dict


FactType = Literal["text", "name", "email", "date", "money", "quote"]


class RequiredFact(BaseModel):
    key: str
    type: FactType
    where: str


class Criterion(BaseModel):
    id: str
    text: str


class Spec(BaseModel):
    goal: str
    sop: str
    kind: Literal["change", "question"]
    required_facts: list[RequiredFact]
    criteria: list[Criterion]
    plan: list[str]
    assumptions: list[str]
    questions: list[str]


# ---------- helpers ----------

_client = None


def llm() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = wrap_openai(AsyncOpenAI())
    return _client


def _model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-6.1-sol")


def _count(counters: dict, resp) -> None:
    counters["tokens_in"] = counters.get("tokens_in", 0) + resp.usage.input_tokens
    counters["tokens_out"] = counters.get("tokens_out", 0) + resp.usage.output_tokens


async def _browser(state: dict, config: dict):
    """The session's browser, synced with state. Restarted (and re-opened at the last page) if needed."""
    cfg = config["configurable"]
    browser, fresh = await get_browser(cfg["thread_id"], COMPANY, cfg.get("headed", False))
    browser.mode = state.get("mode", "work")
    browser.approved = {d["key_hash"] for d in state.get("decisions", []) if d["decision"] == "approved"}
    if fresh and state.get("url"):
        await browser.goto(state["url"])
        return browser, "The browser was restarted; anything typed but not submitted was lost."
    return browser, ""


# ---------- nodes ----------

async def understand(state: State, config) -> dict:
    resp = await llm().responses.parse(
        model=_model(), instructions=prompts.understand_instructions(COMPANY), input=state["request"],
        text_format=Spec, reasoning={"effort": "medium"},
    )
    spec: Spec = resp.output_parsed
    counters = {"work": 0, "verify": 0, "repair": 0, "consec_fail": 0}
    _count(counters, resp)
    browser, _ = await _browser({}, config)
    obs = await browser.observe()
    pending = {"kind": "question", "question": "\n".join(spec.questions)} if spec.questions else None
    return {"spec": spec.model_dump(), "mode": "work", "status": "running", "counters": counters, "pending": pending,
            "obs": obs.snapshot, "page_text": obs.page_text, "url": obs.url}


async def act(state: State, config) -> dict:
    mode, counters = state["mode"], dict(state["counters"])
    if counters.get(mode, 0) >= BUDGET[mode]:
        return {"status": "budget_exhausted"}
    browser, note = await _browser(state, config)
    updates = {}
    if note:
        obs = await browser.observe()
        updates = {"obs": obs.snapshot, "page_text": obs.page_text, "url": obs.url}
    resp = await llm().responses.create(
        model=_model(),
        instructions=prompts.instructions(COMPANY, mode),
        input=prompts.context({**state, **updates}, mode, note),
        tools=WORK_TOOLS if mode == "work" else VERIFY_TOOLS,
        tool_choice="required",
        parallel_tool_calls=False,
        reasoning={"effort": os.getenv("OPENAI_EFFORT_ACT", "low")},
    )
    _count(counters, resp)
    call = next((item for item in resp.output if item.type == "function_call"), None)
    proposed = {"tool": call.name, "args": json.loads(call.arguments)} if call else {"tool": "none", "args": {}}
    return {**updates, "proposed": proposed, "counters": counters}


async def execute(state: State, config) -> dict:
    browser, _ = await _browser(state, config)
    return await run_tool(state["proposed"], state, browser, COMPANY, RUNS_DIR / config["configurable"]["thread_id"])


def human(state: State) -> dict:
    pending = state["pending"]
    answer = interrupt(pending)  # pauses here; resumes with Command(resume=answer)
    counters = {**state["counters"], "consec_fail": 0}
    step = {"n": len(state.get("steps", [])) + 1, "at": time.time(), "mode": state["mode"], "tool": "human", "why": "",
            "url": state.get("url"), "screenshot": None}
    if pending["kind"] == "approval":
        approved = answer.get("decision") == "approve"
        decision = {k: pending[k] for k in ("rule", "description", "payload", "key_hash")}
        decision.update(decision="approved" if approved else "denied", note=answer.get("note", ""))
        message = ("Approved by a human; repeating the held action." if approved
                   else "Denied by a human" + (f": {decision['note']}" if decision["note"] else "") + ". Don't retry it.")
        step.update(args=decision, outcome=decision["decision"], message=message)
        return {"pending": None, "human_decision": decision["decision"], "decisions": [decision],
                "steps": [step], "counters": counters}
    text = str(answer.get("answer", ""))
    answer_id = f"answer:{len(state.get('answers', [])) + 1}"
    step.update(args={"question": pending["question"], "answer": text}, outcome="answered",
                message=f"Human answered ({answer_id}): {text}")
    return {"pending": None, "human_decision": "answered", "answers": [text], "steps": [step], "counters": counters}


async def report(state: State, config) -> dict:
    await close_browser(config["configurable"]["thread_id"])
    return {"report": build_report(state)}


# ---------- wiring ----------

def _after_understand(state: State) -> str:
    return "human" if state.get("pending") else "act"


def _after_act(state: State) -> str:
    return "execute" if state["status"] == "running" else "report"


def _after_execute(state: State) -> str:
    if state.get("pending"):
        return "human"
    return "act" if state["status"] == "running" else "report"


def _after_human(state: State) -> str:
    return "execute" if state.get("human_decision") == "approved" else "act"


def build_graph(checkpointer):
    g = StateGraph(State)
    for name, node in [("understand", understand), ("act", act), ("execute", execute), ("human", human), ("report", report)]:
        g.add_node(name, node)
    g.add_edge(START, "understand")
    g.add_conditional_edges("understand", _after_understand, ["human", "act"])
    g.add_conditional_edges("act", _after_act, ["execute", "report"])
    g.add_conditional_edges("execute", _after_execute, ["human", "act", "report"])
    g.add_conditional_edges("human", _after_human, ["execute", "act"])
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)


def new_thread_id() -> str:
    return datetime.now().strftime("run-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def run_config(thread_id: str, headed: bool = False, scenario: str | None = None) -> dict:
    return {
        "configurable": {"thread_id": thread_id, "headed": headed},
        "metadata": {"thread_id": thread_id, "scenario": scenario},  # groups traces into a LangSmith Thread
        "run_name": "ai-worker",
        "recursion_limit": 400,
    }


async def advance(graph, payload, config: dict, on_update=None) -> dict:
    """Run until the task finishes or pauses for a human."""
    async for chunk in graph.astream(payload, config, stream_mode="updates"):
        if on_update:
            on_update(chunk)
    snap = await graph.aget_state(config)
    pending = next((i.value for t in snap.tasks for i in t.interrupts), None)
    return {"state": snap.values, "pending": pending, "done": not snap.next}
