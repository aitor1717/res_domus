"""
Fixtures for browser-driven (Playwright) e2e tests. Unlike the rest of
tests/ (which drive the app via Flask's in-process test_client), these spin
up a real Flask dev server subprocess and a real Chromium instance - needed
for anything that depends on actual CSS layout or JS execution, which a
test_client request/response cycle can't exercise.

Requires `pip install -r requirements-dev.txt` followed by a one-time
`playwright install chromium`. Excluded from the default `pytest tests/`
run (see ../../pytest.ini) - run explicitly with `pytest tests/e2e -v`.

Every server here runs under its own TEST_RUN-isolated data dir (same
mechanism as the rest of the suite - see tests/conftest.py) and NTFY_TOPIC
is forced blank so a scripted browser action never fires a real push
notification to the developer's phone.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = APP_DIR.parent / "data"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception as e:  # noqa: BLE001 - just polling for readiness
            last_err = e
            time.sleep(0.3)
    raise RuntimeError(f"Server at {url} never became ready: {last_err}")


def _start_server(demo_mode: bool):
    run_name = f"e2e_{uuid.uuid4().hex[:8]}"
    port = _free_port()
    env = {
        **os.environ,
        "TEST_RUN": run_name,
        "BASIC_AUTH_USER": "",
        "BASIC_AUTH_PASS": "",
        "ANTHROPIC_API_KEY": "",
        "NTFY_TOPIC": "",
        "DEMO_MODE": "1" if demo_mode else "0",
        "FLASK_DEBUG": "0",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "app", "run", "--port", str(port)],
        cwd=str(APP_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base_url + "/", timeout=60)
    except Exception:
        proc.terminate()
        proc.wait(timeout=5)
        shutil.rmtree(DATA_DIR / "test_runs" / run_name, ignore_errors=True)
        raise
    return proc, base_url, run_name


@pytest.fixture
def live_server():
    """A real Flask dev server, isolated data, DEMO_MODE off."""
    proc, base_url, run_name = _start_server(demo_mode=False)
    try:
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(DATA_DIR / "test_runs" / run_name, ignore_errors=True)


@pytest.fixture
def live_demo_server():
    """A real Flask dev server, isolated data, DEMO_MODE on, seeded from
    the committed English demo DB so pages have real content to lay out."""
    run_name = f"e2e_{uuid.uuid4().hex[:8]}"
    seed_db = DATA_DIR / "res_domus_demo_en.db"
    target_dir = DATA_DIR / "test_runs" / run_name
    target_dir.mkdir(parents=True, exist_ok=True)
    if seed_db.exists():
        shutil.copy(seed_db, target_dir / "res_domus.db")

    port = _free_port()
    env = {
        **os.environ,
        "TEST_RUN": run_name,
        "BASIC_AUTH_USER": "",
        "BASIC_AUTH_PASS": "",
        "ANTHROPIC_API_KEY": "",
        "NTFY_TOPIC": "",
        "DEMO_MODE": "1",
        "FLASK_DEBUG": "0",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "app", "run", "--port", str(port)],
        cwd=str(APP_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base_url + "/", timeout=60)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(target_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()
