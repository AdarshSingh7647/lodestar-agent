"""Thin, stdlib-only bridge between Claude Code hooks and the local daemon.

Usage (from hooks.json):   python shim.py <EventName>      (hook JSON on stdin)
Usage (from /manager cmd): python shim.py cli <args...>   (runs mgr.cli in the project)

Fails open: if the daemon cannot be reached, the hook prints nothing and exits 0,
so Claude Code is never blocked by a missing manager.
"""
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("MGR_HOME") or (Path.home() / ".mgr"))


def load_cfg():
    return json.loads((HOME / "config.json").read_text(encoding="utf-8"))


def post(cfg, path, payload, timeout):
    req = urllib.request.Request(
        f"http://127.0.0.1:{cfg['port']}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Mgr-Token": cfg["token"]},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def spawn_daemon(cfg):
    HOME.mkdir(parents=True, exist_ok=True)
    log = open(HOME / "daemon.log", "ab")
    flags = (0x00000008 | 0x00000200) if os.name == "nt" else 0
    subprocess.Popen([sys.executable, "-m", "mgr.daemon"], cwd=cfg["project_root"], stdout=log,
                     stderr=log, stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True,
                     env={**os.environ, "PYTHONPATH": cfg["project_root"]})


def run_cli(cfg, args):
    r = subprocess.run([sys.executable, "-m", "mgr.cli", *args], cwd=cfg["project_root"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


def main(argv):
    if not argv:
        return 0
    try:
        cfg = load_cfg()
    except (OSError, ValueError):
        print("Smart Agent Manager is not installed. Run: python -m mgr.cli install", file=sys.stderr)
        return 0

    if argv[0] == "cli":
        extra = shlex.split(" ".join(argv[1:]), posix=True) if len(argv) > 1 else []
        return run_cli(cfg, extra)

    event = argv[0]
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    timeout = 170 if event == "Stop" else 20

    # Retry only when nothing is listening yet (daemon not started). Any error after the request was
    # sent must NOT be retried: Stop hooks are not idempotent and would re-run verification.
    for attempt in range(12):
        try:
            out = post(cfg, f"/hook/{event}", payload, timeout)
        except urllib.error.URLError as e:
            if not isinstance(e.reason, ConnectionRefusedError):
                print(f"Smart Agent Manager hook failed: {e.reason}", file=sys.stderr)
                return 0
        except ConnectionRefusedError:
            pass
        except OSError as e:
            print(f"Smart Agent Manager hook failed: {e}", file=sys.stderr)
            return 0
        else:
            if out:
                print(json.dumps(out))
            return 0
        if attempt == 0:
            spawn_daemon(cfg)
        time.sleep(0.5)
    print("Smart Agent Manager daemon unreachable; continuing without it.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
