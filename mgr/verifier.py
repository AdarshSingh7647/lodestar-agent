"""Execution-first verification. The command comes from .mgr.json, package.json, or a tests/ folder."""
import json
import subprocess
import sys
from pathlib import Path


def detect_command(cwd):
    root = Path(cwd)
    cfg = root / ".mgr.json"
    if cfg.exists():
        try:
            verify = json.loads(cfg.read_text(encoding="utf-8")).get("verify")
            if verify:
                return verify
        except (OSError, ValueError):
            pass
    pkg = root / "package.json"
    if pkg.exists():
        try:
            if json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}).get("test"):
                return "npm test --silent"
        except (OSError, ValueError):
            pass
    if (root / "tests").is_dir():
        return "python -m unittest discover -s tests -q"
    return None


def run(cwd, timeout=120):
    label = detect_command(cwd)
    if not label:
        return {"checked": False, "passed": None, "command": None, "returncode": None,
                "output": "no verification command configured"}
    kw = dict(cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    try:
        if label.startswith("python -m "):
            # Default checks: run the daemon's own interpreter directly, no cmd.exe involved.
            argv = [sys.executable, "-m"] + label[len("python -m "):].split()
            p = subprocess.run(argv, **kw)
        else:
            # Custom .mgr.json command: the user wrote it for a shell.
            p = subprocess.run(label, shell=True, **kw)
    except subprocess.TimeoutExpired:
        return {"checked": True, "passed": False, "command": label, "returncode": None,
                "output": f"timed out after {timeout}s"}
    return {"checked": True, "passed": p.returncode == 0, "command": label,
            "returncode": p.returncode, "output": (p.stdout or "") + (p.stderr or "")}
