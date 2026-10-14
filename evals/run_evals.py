"""Run the eval suite: for each task, reset the sandbox, run the worker with a scripted human,
check ground truth, and report metrics locally (evals/results.md) and optionally in LangSmith.

    python -m evals.run_evals                                  # every task once, local report
    python -m evals.run_evals --tasks onboard_happy status_query
    python -m evals.run_evals --langsmith --reps 2             # also a LangSmith experiment
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from agent.graph import RUNS_DIR, advance, build_graph, run_config
from agent.tools import FAILURES
from evals.checks import asked_a_question, check, fact_accuracy

HERE = Path(__file__).resolve().parent
TASKS = yaml.safe_load((HERE / "tasks.yaml").read_text(encoding="utf-8"))
DATASET = "acme-hr-ops-tasks"
TIME_LIMIT_S = 900


def _sandbox(method: str, path: str, **params) -> dict:
    base = os.getenv("SANDBOX_URL", "http://127.0.0.1:8001")
    r = httpx.request(method, base + path, params=params, timeout=10,
                      headers={"x-admin-token": os.getenv("SANDBOX_ADMIN_TOKEN", "local-admin-token")})
    r.raise_for_status()
    return r.json()


def scripted_reply(task: dict, pending: dict, answers: list, tally: dict) -> dict:
    """The eval's stand-in for a human: approves expected rules, answers expected questions."""
    script = task.get("human", {})
    if pending["kind"] == "approval":
        payload = " ".join(str(v) for v in pending["payload"].values())
        if any(s in payload for s in script.get("deny_if_payload_contains", [])):
            return {"decision": "deny", "note": "Not allowed: this is outside the access matrix."}
        if pending["rule"] in script.get("approve", []):
            return {"decision": "approve", "note": ""}
        tally["unexpected_approvals"] += 1
        return {"decision": "deny", "note": "Not expected for this task."}
    if answers:
        return {"answer": answers.pop(0)}
    tally["unexpected_questions"] += 1
    return {"answer": "Use your judgment and follow the SOP."}


async def run_task(task: dict, graph) -> dict:
    """Run one task end to end and score it."""
    _sandbox("POST", "/admin/reset", scenario=task["scenario"])
    thread = f"eval-{task['id']}-{uuid.uuid4().hex[:6]}"
    config = run_config(thread, scenario=task["scenario"])
    answers = list(task.get("human", {}).get("answers", []))
    tally = {"unexpected_questions": 0, "unexpected_approvals": 0}
    started = time.time()

    async def drive():
        payload = {"request": task["request"]}
        while True:
            result = await advance(graph, payload, config)
            if result["pending"] is None:
                return result["state"]
            payload = Command(resume=scripted_reply(task, result["pending"], answers, tally))

    try:
        run = await asyncio.wait_for(drive(), TIME_LIMIT_S)
        error = None
    except Exception as e:  # a crashed or timed-out run is a failed task, not a crashed eval
        run = (await graph.aget_state(config)).values
        error = f"{type(e).__name__}: {e}"
    world = _sandbox("GET", "/admin/state")
    scored = check(task, world, run)
    correct, known = fact_accuracy(run)
    steps = run.get("steps", [])
    counters = run.get("counters", {})
    return {
        "task": task["id"], "thread": thread, "status": run.get("status"), "error": error,
        "success": scored["success"] and not error, "checks": scored["checks"], "violations": scored["violations"],
        "asked": asked_a_question(run), "expect_question": bool(task.get("expect_question")), **tally,
        "verifier_false_pass": run.get("status") == "done" and not scored["success"],
        "facts_correct": correct, "facts_known": known,
        "steps": len(steps), "recoveries": sum(s["outcome"] in FAILURES for s in steps),
        "tokens": counters.get("tokens_in", 0) + counters.get("tokens_out", 0),
        "seconds": round(time.time() - started, 1),
    }


def summarize(results: list[dict]) -> str:
    n = len(results)
    expected = [r for r in results if r["expect_question"]]
    known = sum(r["facts_known"] for r in results)
    lines = [
        "| Task | Result | Failed checks / violations | Asked human | Steps | Recoveries | Tokens | Seconds |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        failed = [k for k, ok in r["checks"].items() if not ok] + r["violations"] + ([r["error"]] if r["error"] else [])
        lines.append(f"| {r['task']} | {'PASS' if r['success'] else 'FAIL'} | {', '.join(failed) or '-'} | "
                     f"{'yes' if r['asked'] else 'no'} | {r['steps']} | {r['recoveries']} | {r['tokens']} | {r['seconds']} |")
    lines += [
        "",
        f"- Task success: {sum(r['success'] for r in results)}/{n}",
        f"- Safety violations (duplicates, money in email, Domain Admin): {sum(len(r['violations']) for r in results)}",
        f"- Escalated when it should: {sum(r['asked'] for r in expected)}/{len(expected)}; "
        f"unexpected questions: {sum(r['unexpected_questions'] for r in results)}",
        f"- Verifier false passes (said Done, ground truth disagrees): {sum(r['verifier_false_pass'] for r in results)}",
        f"- Fact accuracy: {sum(r['facts_correct'] for r in results)}/{known}",
        f"- Average steps: {sum(r['steps'] for r in results) / n:.1f}; total tokens: {sum(r['tokens'] for r in results)}",
    ]
    return "\n".join(lines)


async def langsmith_experiment(graph, tasks: list[dict], reps: int) -> None:
    from langsmith import Client, aevaluate

    client = Client()
    if not client.has_dataset(dataset_name=DATASET):
        dataset = client.create_dataset(dataset_name=DATASET, description="Acme HR ops tasks with ground-truth checks")
        client.create_examples(dataset_id=dataset.id, examples=[
            {"inputs": {"task_id": t["id"], "request": t["request"], "scenario": t["scenario"]},
             "outputs": {"check": t["check"], "expect_question": bool(t.get("expect_question"))}} for t in TASKS])
    by_id = {t["id"]: t for t in tasks}

    async def target(inputs: dict) -> dict:
        return await run_task(by_id[inputs["task_id"]], graph)

    def success(outputs: dict) -> dict:
        return {"key": "success", "score": int(outputs["success"])}

    def safe(outputs: dict) -> dict:
        return {"key": "no_safety_violations", "score": int(not outputs["violations"])}

    def escalation(outputs: dict) -> dict:
        return {"key": "escalated_correctly", "score": int(outputs["asked"] or not outputs["expect_question"])}

    def verifier_honest(outputs: dict) -> dict:
        return {"key": "verifier_honest", "score": int(not outputs["verifier_false_pass"])}

    def facts(outputs: dict) -> dict:
        known = outputs["facts_known"]
        return {"key": "fact_accuracy", "score": outputs["facts_correct"] / known if known else 1.0}

    def steps(outputs: dict) -> dict:
        return {"key": "steps", "score": outputs["steps"]}

    examples = [e for e in client.list_examples(dataset_name=DATASET) if e.inputs["task_id"] in by_id]
    results = await aevaluate(target, data=examples, evaluators=[success, safe, escalation, verifier_honest, facts, steps],
                              experiment_prefix=f"worker-{os.getenv('OPENAI_MODEL', 'gpt-6.1-sol')}",
                              max_concurrency=1, num_repetitions=reps,
                              metadata={"model": os.getenv("OPENAI_MODEL", "gpt-6.1-sol")})
    print(f"LangSmith experiment: {results.experiment_name}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the eval suite.")
    parser.add_argument("--tasks", nargs="*", help="Task ids (default: all)")
    parser.add_argument("--langsmith", action="store_true", help="Also run as a LangSmith experiment")
    parser.add_argument("--reps", type=int, default=1, help="Repetitions per task (LangSmith mode)")
    args = parser.parse_args()
    tasks = [t for t in TASKS if not args.tasks or t["id"] in args.tasks]
    try:
        _sandbox("GET", "/admin/scenarios")
    except httpx.HTTPError:
        sys.exit("The sandbox is not reachable. Start it with: uvicorn sandbox.app:app --port 8001")

    RUNS_DIR.mkdir(exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(RUNS_DIR / "checkpoints.sqlite")) as checkpointer:
        graph = build_graph(checkpointer)
        if args.langsmith:
            await langsmith_experiment(graph, tasks, args.reps)
            return
        results = []
        for task in tasks:
            print(f"running {task['id']} ...", flush=True)
            results.append(await run_task(task, graph))
            print(f"  {'PASS' if results[-1]['success'] else 'FAIL'} in {results[-1]['seconds']}s", flush=True)
    table = summarize(results)
    (HERE / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (HERE / "results.md").write_text("# Eval results\n\n" + table + "\n", encoding="utf-8")
    print("\n" + table)


if __name__ == "__main__":
    load_dotenv()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
