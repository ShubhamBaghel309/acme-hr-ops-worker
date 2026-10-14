import os
import subprocess
import sys
import time

import httpx
import pytest

SANDBOX_PORT = 8021
SANDBOX_URL = f"http://127.0.0.1:{SANDBOX_PORT}"
# Same fake test credentials as .env.example (the sandbox uses these defaults too).
os.environ.setdefault("SANDBOX_USER", "ops.bot@acme.example")
os.environ.setdefault("SANDBOX_PASSWORD", "acme-sandbox-pass")
ADMIN = {"x-admin-token": os.getenv("SANDBOX_ADMIN_TOKEN", "local-admin-token")}


@pytest.fixture(scope="session")
def sandbox_url():
    """A real sandbox server for browser tests (separate port from the dev server)."""
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "sandbox.app:app", "--port", str(SANDBOX_PORT)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 30
    while True:
        try:
            httpx.get(SANDBOX_URL + "/login", timeout=2)
            break
        except httpx.TransportError:
            if time.time() > deadline:
                proc.terminate()
                raise RuntimeError("sandbox did not start")
            time.sleep(0.3)
    os.environ["SANDBOX_URL"] = SANDBOX_URL
    yield SANDBOX_URL
    proc.terminate()


def reset(scenario: str) -> None:
    httpx.post(f"{SANDBOX_URL}/admin/reset?scenario={scenario}", headers=ADMIN).raise_for_status()
