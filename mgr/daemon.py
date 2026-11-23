"""Local daemon: hook endpoint + web UI, bound to 127.0.0.1 and guarded by a token."""
import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .manager import Manager
from .router import Router
from .store import Store, home

UI = Path(__file__).with_name("ui.html")


class Server(ThreadingHTTPServer):
    daemon_threads = True


def make_server(port, token, mgr: Manager):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, obj=None, raw=None, ctype="application/json"):
            body = raw if raw is not None else json.dumps(obj if obj is not None else {}).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            supplied = self.headers.get("X-Mgr-Token") or parse_qs(urlparse(self.path).query).get("t", [""])[0]
            return secrets.compare_digest(supplied.encode(), token.encode())

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                return self._send(200, raw=UI.read_bytes(), ctype="text/html; charset=utf-8")
            if path == "/api/state":
                if not self._authorized():
                    return self._send(401, {"error": "bad token"})
                return self._send(200, mgr.state())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._authorized():
                return self._send(401, {"error": "bad token"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid json"})
            if path.startswith("/hook/"):
                try:
                    out = mgr.handle(path[len("/hook/"):], payload)
                except Exception as e:  # fail open: a manager bug must never wedge Claude Code
                    mgr.s.event("hook_error", {"event": path, "error": repr(e)})
                    out = {"systemMessage": f"Smart Agent Manager error (hook skipped): {e!r}"}
                return self._send(200, out or {})
            if path == "/api/questions":
                return self._send(200, {"ids": mgr.enqueue_questions(payload.get("batch", "batch"),
                                                                     payload.get("questions", []))})
            if path == "/api/answer":
                mgr.answer(int(payload["id"]), str(payload["answer"]))
                return self._send(200, {"ok": True})
            if path == "/api/forget":
                mgr.mem.forget(int(payload["id"]))
                return self._send(200, {"ok": True})
            if path == "/api/shutdown":
                self._send(200, {"ok": True})
                threading.Thread(target=srv.shutdown, daemon=True).start()
                return None
            self._send(404, {"error": "not found"})

    srv = Server(("127.0.0.1", port), Handler)
    return srv


def main():
    cfg = json.loads((home() / "config.json").read_text(encoding="utf-8"))
    mgr = Manager(Store(), Router(cfg.get("roles", {})))
    try:
        srv = make_server(cfg["port"], cfg["token"], mgr)
    except OSError as e:
        print(f"cannot bind 127.0.0.1:{cfg['port']}: {e}", file=sys.stderr)
        return 1
    (home() / "daemon.pid").write_text(str(os.getpid()), encoding="utf-8")
    print(f"Smart Agent Manager listening on http://127.0.0.1:{cfg['port']}", flush=True)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
