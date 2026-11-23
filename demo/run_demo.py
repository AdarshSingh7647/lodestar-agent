"""End-to-end demo of the hook path, without needing a live Claude session.

Every step goes through the real stack: shim.py (subprocess) -> HTTP -> daemon -> manager ->
verifier (runs the toy repo's unittest suite). Only the agent's code edit is simulated.

Run:  python demo/run_demo.py
"""
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mgr.daemon import make_server  # noqa: E402
from mgr.manager import Manager  # noqa: E402
from mgr.router import Router  # noqa: E402
from mgr.store import Store  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_demo(log=print):
    tmp = Path(tempfile.mkdtemp(prefix="mgr-demo-"))
    home = tmp / "home"
    home.mkdir()
    repo = tmp / "toy_repo"
    shutil.copytree(ROOT / "demo" / "toy_repo", repo)

    port, token = free_port(), secrets.token_urlsafe(16)
    (home / "config.json").write_text(json.dumps(
        {"port": port, "token": token, "project_root": str(ROOT), "roles": {}}), encoding="utf-8")

    store = Store(home / "mgr.db")
    srv = make_server(port, token, Manager(store, Router({})))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = {**os.environ, "MGR_HOME": str(home)}
    steps = []

    def hook(event, payload):
        body = {"session_id": "demo", "cwd": str(repo), **payload}
        p = subprocess.run([sys.executable, str(ROOT / "plugin" / "hooks" / "shim.py"), event],
                           input=json.dumps(body), capture_output=True, text=True, env=env, timeout=200)
        out = json.loads(p.stdout) if p.stdout.strip() else None
        steps.append({"event": event, "output": out, "stderr": p.stderr.strip()})
        log(f"[{event}] {json.dumps(out) if out else '(no decision)'}")
        return out

    try:
        hook("SessionStart", {"source": "startup"})
        hook("UserPromptSubmit", {"prompt": "Always use pathlib instead of os.path for file paths."})
        hook("UserPromptSubmit", {"prompt": "Fix the add function in calc.py so the tests pass."})
        first_stop = hook("Stop", {"last_assistant_message": "Done. The add function is fixed.",
                                   "stop_hook_active": False})
        # The agent's fix, simulated (in real use Claude Code makes this edit):
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        second_stop = hook("Stop", {"last_assistant_message": "Fixed add to return a + b.",
                                    "stop_hook_active": True})
        hook("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}})
        new_session = hook("SessionStart", {"source": "resume"})
        tasks = store.q("select id, title, status, rounds from tasks")
    finally:
        srv.shutdown()
        srv.server_close()
        store.close()

    return {"steps": steps, "first_stop": first_stop, "second_stop": second_stop,
            "new_session": new_session, "tasks": tasks, "workdir": str(tmp)}


if __name__ == "__main__":
    result = run_demo()
    print("\nFinal tasks:")
    for t in result["tasks"]:
        print(f"  #{t['id']} [{t['status']}] rounds={t['rounds']}  {t['title']}")
    print(f"Work dir: {result['workdir']}")
