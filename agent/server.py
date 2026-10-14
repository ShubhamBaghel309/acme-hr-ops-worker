"""HTTP API for the console: start runs, watch them, answer approvals and questions.

    python -m agent.server        (port 8000; set HEADED=1 to watch the browser)

Started without --reload on purpose: on Windows, uvicorn's reload mode switches to an event loop
that cannot launch the browser. Runs execute as background tasks, one at a time; their state lives
in the LangGraph checkpointer, so a restarted server picks up paused or interrupted sessions.
"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel

from agent.graph import RUNS_DIR, advance, build_graph, new_thread_id, run_config

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
busy = asyncio.Lock()           # one active run at a time
tasks: dict[str, asyncio.Task] = {}
errors: dict[str, str] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    RUNS_DIR.mkdir(exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(RUNS_DIR / "checkpoints.sqlite")) as checkpointer:
        await checkpointer.setup()  # creates the tables, so listing sessions works before the first run
        app.state.checkpointer = checkpointer
        app.state.graph = build_graph(checkpointer)
        yield


app = FastAPI(title="AI worker API", lifespan=lifespan)


class StartRun(BaseModel):
    request: str
    scenario: str | None = None
    session_id: str | None = None


class Resume(BaseModel):
    decision: str | None = None  # approve | deny (for approvals)
    note: str = ""
    answer: str | None = None    # for questions


def _config(thread_id: str, scenario: str | None = None) -> dict:
    return run_config(thread_id, headed=os.getenv("HEADED") == "1", scenario=scenario)


async def _sandbox(method: str, path: str, **params):
    base = os.getenv("SANDBOX_URL", "http://127.0.0.1:8001")
    async with httpx.AsyncClient() as client:
        r = await client.request(method, base + path, params=params,
                                 headers={"x-admin-token": os.getenv("SANDBOX_ADMIN_TOKEN", "local-admin-token")})
    r.raise_for_status()
    return r.json()


async def _drive(thread_id: str, payload, config: dict) -> None:
    async with busy:
        errors.pop(thread_id, None)
        try:
            await advance(app.state.graph, payload, config)
        except Exception as e:  # surface failures in the console instead of losing them in a background task
            errors[thread_id] = f"{type(e).__name__}: {e}"


def _start(thread_id: str, payload, config: dict) -> None:
    if busy.locked():
        raise HTTPException(409, "Another run is in progress.")
    tasks[thread_id] = asyncio.create_task(_drive(thread_id, payload, config))


@app.get("/api/scenarios")
async def scenarios():
    return await _sandbox("GET", "/admin/scenarios")


@app.post("/api/runs")
async def start_run(body: StartRun):
    if body.scenario:
        await _sandbox("POST", "/admin/reset", scenario=body.scenario)
    thread_id = body.session_id or new_thread_id()
    _start(thread_id, {"request": body.request}, _config(thread_id, body.scenario))
    return {"thread_id": thread_id}


@app.get("/api/runs")
async def list_runs():
    query = "SELECT thread_id, MAX(checkpoint_id) AS last FROM checkpoints GROUP BY thread_id ORDER BY last DESC LIMIT 30"
    async with app.state.checkpointer.conn.execute(query) as cursor:
        threads = [row[0] for row in await cursor.fetchall()]
    runs = []
    for thread_id in threads:
        values = (await app.state.graph.aget_state(_config(thread_id))).values
        runs.append({"thread_id": thread_id, "request": values.get("request"), "status": values.get("status")})
    return runs


@app.get("/api/runs/{thread_id}")
async def get_run(thread_id: str):
    snap = await app.state.graph.aget_state(_config(thread_id))
    if not snap.values:
        raise HTTPException(404, "No such session.")
    v = snap.values
    task = tasks.get(thread_id)
    return {
        "thread_id": thread_id,
        "request": v.get("request"),
        "status": v.get("status"),
        "mode": v.get("mode"),
        "spec": v.get("spec"),
        "facts": v.get("facts", {}),
        "verdicts": v.get("verdicts", {}),
        "steps": v.get("steps", []),
        "pending": next((i.value for t in snap.tasks for i in t.interrupts), None),
        "report": v.get("report"),
        "active": bool(task and not task.done()),
        "interrupted": bool(snap.next) and not (task and not task.done()),  # stopped mid-run, e.g. a server restart
        "error": errors.get(thread_id),
    }


@app.post("/api/runs/{thread_id}/resume")
async def resume(thread_id: str, body: Resume):
    config = _config(thread_id)
    snap = await app.state.graph.aget_state(config)
    pending = next((i.value for t in snap.tasks for i in t.interrupts), None)
    if pending:
        value = ({"decision": body.decision, "note": body.note} if pending["kind"] == "approval"
                 else {"answer": body.answer or ""})
        _start(thread_id, Command(resume=value), config)
    elif snap.next:  # interrupted mid-run (e.g. the server stopped): continue from the last checkpoint
        _start(thread_id, None, config)
    else:
        raise HTTPException(409, "This session has nothing to resume.")
    return {"ok": True}


app.mount("/runs", StaticFiles(directory=RUNS_DIR, check_dir=False), name="runs")
if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")


if __name__ == "__main__":
    load_dotenv()
    sys.stdout.reconfigure(encoding="utf-8")
    RUNS_DIR.mkdir(exist_ok=True)
    uvicorn.run(app, host="127.0.0.1", port=8000)
