"""Run a task from the terminal, answering approvals and questions inline.

    python -m agent.cli "<what you want done>" --scenario <sandbox scenario> --headed
    python -m agent.cli --thread <session id>      # resume a paused or interrupted session
"""
import argparse
import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from agent.graph import RUNS_DIR, advance, build_graph, new_thread_id, run_config
from agent.report import to_markdown


def reset_sandbox(scenario: str) -> None:
    base = os.getenv("SANDBOX_URL", "http://127.0.0.1:8001")
    try:
        r = httpx.post(f"{base}/admin/reset", params={"scenario": scenario},
                       headers={"x-admin-token": os.getenv("SANDBOX_ADMIN_TOKEN", "local-admin-token")})
    except httpx.TransportError:
        sys.exit(f"The sandbox is not running at {base}. Start it with: uvicorn sandbox.app:app --port 8001")
    r.raise_for_status()


def print_update(chunk: dict) -> None:
    for node, update in chunk.items():
        if not isinstance(update, dict):
            continue
        if node == "understand":
            spec = update["spec"]
            print(f"\n[understand] SOP: {spec['sop']} ({spec['kind']})\nGoal: {spec['goal']}")
            print("\n".join(f"  {i}. {p}" for i, p in enumerate(spec["plan"], start=1)))
            print("\n".join(f"  {c['id']}: {c['text']}" for c in spec["criteria"]))
        for s in update.get("steps", []):
            args = ", ".join(f"{k}={str(v)[:70]!r}" for k, v in s["args"].items()) if s["tool"] != "human" else ""
            print(f"#{s['n']:>2} [{s['mode']}] {s['tool']}({args}) -> {s['outcome']}")
            if s["why"]:
                print(f"      why: {s['why']}")
            if s["message"]:
                print(f"      {s['message']}")


async def ask(pending: dict) -> dict:
    if pending["kind"] == "approval":
        print(f"\n=== APPROVAL NEEDED: {pending['description']} ===")
        for key, value in pending["payload"].items():
            print(f"  {key}: {value}")
        yes = (await asyncio.to_thread(input, "Approve? [y/N] ")).strip().lower() == "y"
        note = "" if yes else await asyncio.to_thread(input, "Reason (optional): ")
        return {"decision": "approve" if yes else "deny", "note": note}
    print(f"\n=== QUESTION FROM THE WORKER ===\n{pending['question']}")
    return {"answer": await asyncio.to_thread(input, "Your answer: ")}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI worker on one task.")
    parser.add_argument("task", nargs="?", help="What you want done, in plain English")
    parser.add_argument("--scenario", help="Reset the sandbox to this scenario first")
    parser.add_argument("--thread", help="Resume this session instead of starting a new one")
    parser.add_argument("--headed", action="store_true", help="Show the browser")
    args = parser.parse_args()
    if not args.task and not args.thread:
        parser.error("give a task, or --thread to resume a session")
    if args.scenario:
        reset_sandbox(args.scenario)

    thread = args.thread or new_thread_id()
    config = run_config(thread, args.headed, args.scenario)
    RUNS_DIR.mkdir(exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(RUNS_DIR / "checkpoints.sqlite")) as checkpointer:
        graph = build_graph(checkpointer)
        payload = {"request": args.task}
        if args.thread:
            snap = await graph.aget_state(config)
            pending = next((i.value for t in snap.tasks for i in t.interrupts), None)
            payload = Command(resume=await ask(pending)) if pending else None
        print(f"Session: {thread}")
        while True:
            result = await advance(graph, payload, config, print_update)
            if result["pending"] is None:
                break
            payload = Command(resume=await ask(result["pending"]))
    print("\n" + to_markdown(result["state"]["report"]))


if __name__ == "__main__":
    load_dotenv()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
