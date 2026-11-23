"""Command line: install, start/stop the daemon, remember facts, list tasks."""
import argparse
import glob
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .memory import Memory
from .store import Store, home

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "smart-agent-manager"
MARKETPLACE = "smart-agent-manager-local"
ALIASES = {"on": "start", "off": "stop", "ui": "status"}


def cfg_file():
    return home() / "config.json"


def load_cfg():
    return json.loads(cfg_file().read_text(encoding="utf-8"))


def api(cfg, method, path, body=None, timeout=5):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{cfg['port']}{path}", data=data, method=method,
                                 headers={"X-Mgr-Token": cfg["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def alive(cfg):
    try:
        api(cfg, "GET", "/api/state", timeout=2)
        return True
    except Exception:
        return False


def spawn_daemon(cfg):
    log = open(home() / "daemon.log", "ab")
    flags = (0x00000008 | 0x00000200) if os.name == "nt" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, "-m", "mgr.daemon"], cwd=cfg["project_root"], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True,
                     env={**os.environ, "PYTHONPATH": cfg["project_root"]})


def find_claude():
    found = shutil.which("claude") or os.environ.get("MGR_CLAUDE_BIN")
    if found:
        return found
    hits = sorted(glob.glob(str(Path.home() / ".vscode" / "extensions" / "anthropic.claude-code-*"
                                / "resources" / "native-binary" / "claude*")))
    return hits[-1] if hits else None


def cmd_install(a):
    home().mkdir(parents=True, exist_ok=True)
    old = load_cfg() if cfg_file().exists() else {}
    cfg = {"port": a.port, "token": old.get("token") or secrets.token_urlsafe(24),
           "project_root": str(PROJECT_ROOT), "roles": old.get("roles", {})}
    cfg_file().write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"Wrote {cfg_file()}")
    claude = a.claude_bin or find_claude()
    if not a.register:
        print(f'Register the plugin:\n  claude plugin marketplace add "{PROJECT_ROOT}"\n'
              f"  claude plugin install {PLUGIN_NAME}@{MARKETPLACE}")
        return 0
    if not claude:
        print("Claude Code CLI not found. Pass --claude-bin PATH.")
        return 1
    steps = [[claude, "plugin", "marketplace", "add", str(PROJECT_ROOT)],
             [claude, "plugin", "install", f"{PLUGIN_NAME}@{MARKETPLACE}"]]
    for argv in steps:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(f"$ {' '.join(argv[1:])}\n{(r.stdout + r.stderr).strip()}")
        if r.returncode:
            return r.returncode
    print("Restart Claude Code, then run /manager on")
    return 0


def cmd_start(a):
    cfg = load_cfg()
    if alive(cfg):
        print("already running")
    else:
        spawn_daemon(cfg)
        for _ in range(40):
            if alive(cfg):
                break
            time.sleep(0.25)
        else:
            print("daemon did not start; see " + str(home() / "daemon.log"))
            return 1
        print("started")
    print(f"UI: http://127.0.0.1:{cfg['port']}/?t={cfg['token']}")
    return 0


def cmd_status(a):
    cfg = load_cfg()
    if not alive(cfg):
        print("not running (use /manager on)")
        return 1
    s = api(cfg, "GET", "/api/state")
    open_ = [t for t in s["tasks"] if t["status"] == "open"]
    print(f"running: {len(s['tasks'])} tasks ({len(open_)} open), {len(s['memories'])} memories")
    print(f"UI: http://127.0.0.1:{cfg['port']}/?t={cfg['token']}")
    return 0


def cmd_stop(a):
    cfg = load_cfg()
    if not alive(cfg):
        print("not running")
        return 0
    api(cfg, "POST", "/api/shutdown", {})
    print("stopped")
    return 0


def cmd_remember(a):
    store = Store()
    mid, new = Memory(store).add(a.text, scope=a.scope, project=os.getcwd() if a.scope == "project" else None,
                                 kind=a.kind, key=a.key, source="cli")
    print(("stored" if new else "already known") + f" memory #{mid}")
    return 0


def cmd_tasks(a):
    for t in Store().q("select id, status, rounds, max_rounds, title from tasks order by updated desc limit 20"):
        print(f"#{t['id']} [{t['status']}] {t['rounds']}/{t['max_rounds']}  {t['title']}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="mgr", description="Smart Agent Manager")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("install", help="write config and optionally register the Claude Code plugin")
    i.add_argument("--port", type=int, default=8765)
    i.add_argument("--register", action="store_true")
    i.add_argument("--claude-bin")
    i.set_defaults(fn=cmd_install)

    for name, fn in (("start", cmd_start), ("status", cmd_status), ("stop", cmd_stop), ("tasks", cmd_tasks)):
        sub.add_parser(name).set_defaults(fn=fn)

    r = sub.add_parser("remember", help="store a memory")
    r.add_argument("text")
    r.add_argument("--scope", choices=["user", "project"], default="project")
    r.add_argument("--kind", default="fact")
    r.add_argument("--key")
    r.set_defaults(fn=cmd_remember)

    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in ALIASES:
        raw[0] = ALIASES[raw[0]]
    a = p.parse_args(raw)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
