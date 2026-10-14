"""Print a session's step log from the checkpoint store: python scripts/show_run.py <thread_id>"""
import asyncio
import sys

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agent.graph import RUNS_DIR, build_graph, run_config


async def main(thread_id: str) -> None:
    async with AsyncSqliteSaver.from_conn_string(str(RUNS_DIR / "checkpoints.sqlite")) as cp:
        values = (await build_graph(cp).aget_state(run_config(thread_id))).values
    print("status:", values.get("status"), "| mode:", values.get("mode"))
    for s in values.get("steps", []):
        args = {k: (v if len(str(v)) < 90 else str(v)[:90] + "...") for k, v in s["args"].items()}
        print(f"#{s['n']} [{s['mode']}] {s['tool']} {args} -> {s['outcome']}")
        if s["message"]:
            print("     ", s["message"][:200])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(sys.argv[1]))
