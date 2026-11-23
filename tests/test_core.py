import json
import secrets
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mgr import policy, verifier  # noqa: E402
from mgr.manager import Manager  # noqa: E402
from mgr.memory import Memory  # noqa: E402
from mgr.router import Router  # noqa: E402
from mgr.store import Store  # noqa: E402


def tmp_store():
    d = Path(tempfile.mkdtemp())
    return Store(d / "t.db"), d


class MemoryTests(unittest.TestCase):
    def test_dedupe_identical_content(self):
        s, _ = tmp_store()
        m = Memory(s)
        a, new_a = m.add("use pathlib", project="/p", kind="correction")
        b, new_b = m.add("use pathlib", project="/p", kind="correction")
        self.assertTrue(new_a)
        self.assertFalse(new_b)
        self.assertEqual(a, b)

    def test_same_key_supersedes_and_keeps_history(self):
        s, _ = tmp_store()
        m = Memory(s)
        old, _ = m.add("indent with 2 spaces", scope="user", kind="preference", key="indent")
        new, _ = m.add("indent with 4 spaces", scope="user", kind="preference", key="indent")
        active = [x["id"] for x in m.active("/any")]
        self.assertEqual(active, [new])
        row = s.q("select valid_to, superseded_by from memories where id=?", (old,))[0]
        self.assertIsNotNone(row["valid_to"])
        self.assertEqual(row["superseded_by"], new)

    def test_forget_invalidates(self):
        s, _ = tmp_store()
        m = Memory(s)
        mid, _ = m.add("temporary fact", scope="user", kind="fact")
        m.forget(mid)
        self.assertEqual(m.active("/any"), [])

    def test_retrieve_respects_budget_and_project_scope(self):
        s, _ = tmp_store()
        m = Memory(s)
        m.add("correction for proj a " + "x" * 50, project="/a", kind="correction")
        m.add("correction for proj b " + "y" * 50, project="/b", kind="correction")
        got = m.retrieve("/a", "", budget_chars=200)
        self.assertTrue(all(g["project"] == "/a" for g in got))
        tiny = m.retrieve("/a", "", budget_chars=10)
        self.assertEqual(tiny, [])


class PolicyTests(unittest.TestCase):
    def test_risky_commands_flagged(self):
        self.assertEqual(policy.classify("Bash", {"command": "rm -rf build"}), "recursive delete")
        self.assertEqual(policy.classify("Bash", {"command": "git push --force origin main"}), "force push")
        self.assertEqual(policy.classify("PowerShell", {"command": "Remove-Item x -Recurse"}), "recursive delete")

    def test_safe_and_non_shell_tools_allowed(self):
        self.assertIsNone(policy.classify("Bash", {"command": "ls -la"}))
        self.assertIsNone(policy.classify("Read", {"file_path": "rm -rf"}))


class VerifierTests(unittest.TestCase):
    def _repo(self, verify=None):
        d = Path(tempfile.mkdtemp())
        if verify is not None:
            (d / ".mgr.json").write_text(json.dumps({"verify": verify}), encoding="utf-8")
        return d

    def test_no_command_is_unchecked(self):
        r = verifier.run(self._repo())
        self.assertFalse(r["checked"])
        self.assertIsNone(r["passed"])

    def test_pass_and_fail_by_exit_code(self):
        py = sys.executable
        ok = verifier.run(self._repo(f'"{py}" -c "import sys; sys.exit(0)"'))
        bad = verifier.run(self._repo(f'"{py}" -c "import sys; sys.exit(3)"'))
        self.assertTrue(ok["passed"])
        self.assertFalse(bad["passed"])
        self.assertEqual(bad["returncode"], 3)

    def test_timeout_is_failure(self):
        py = sys.executable
        r = verifier.run(self._repo(f'"{py}" -c "import time; time.sleep(5)"'), timeout=1)
        self.assertTrue(r["checked"])
        self.assertFalse(r["passed"])


class ManagerFlowTests(unittest.TestCase):
    def _setup(self, exit_code):
        s, d = tmp_store()
        repo = d / "repo"
        repo.mkdir()
        (repo / ".mgr.json").write_text(json.dumps(
            {"verify": f'"{sys.executable}" -c "import sys; sys.exit({exit_code})"'}), encoding="utf-8")
        return Manager(s, Router({})), s, str(repo)

    def test_failing_verifier_blocks_then_budget_stops(self):
        mgr, s, cwd = self._setup(1)
        mgr.handle("UserPromptSubmit", {"cwd": cwd, "prompt": "Implement the widget parser"})
        for i in range(1, 5):
            out = mgr.handle("Stop", {"cwd": cwd, "stop_hook_active": i > 1})
            self.assertEqual(out["decision"], "block")
            self.assertIn(f"round {i}/5", out["reason"])
        last = mgr.handle("Stop", {"cwd": cwd})
        self.assertNotIn("decision", last)
        self.assertIn("failed verification", last["systemMessage"])
        self.assertEqual(s.q("select status from tasks")[0]["status"], "needs_human")

    def test_passing_verifier_completes_task(self):
        mgr, s, cwd = self._setup(0)
        mgr.handle("UserPromptSubmit", {"cwd": cwd, "prompt": "Add a README section"})
        out = mgr.handle("Stop", {"cwd": cwd})
        self.assertIn("passed", out["systemMessage"])
        self.assertEqual(s.q("select status from tasks")[0]["status"], "done")

    def test_background_notification_does_not_open_a_task(self):
        mgr, s, cwd = self._setup(0)
        mgr.handle("UserPromptSubmit", {"cwd": cwd, "prompt": "Write the plan to bench_state/plan.md"})
        before = [t["id"] for t in mgr.open_tasks(cwd)]
        out = mgr.handle("UserPromptSubmit", {"cwd": cwd,
                         "prompt": "<task-notification>\n<status>completed</status>\n</task-notification>"})
        self.assertIsNone(out)
        self.assertEqual([t["id"] for t in mgr.open_tasks(cwd)], before)

    def test_one_off_instruction_is_not_stored_as_rule(self):
        mgr, s, cwd = self._setup(0)
        mgr.handle("UserPromptSubmit", {"cwd": cwd, "prompt": "Do NOT run the tests. Just edit and say done."})
        self.assertEqual(Memory(s).active(cwd), [])

    def test_only_the_rule_sentence_is_stored(self):
        mgr, s, cwd = self._setup(0)
        mgr.handle("UserPromptSubmit", {"cwd": cwd,
                   "prompt": "Fix calc.py so the tests pass. Always keep helpers documented."})
        texts = [m["content"] for m in Memory(s).active(cwd)]
        self.assertEqual(texts, ["Always keep helpers documented."])

    def test_stop_without_valid_cwd_never_crashes_or_blocks(self):
        mgr, s, _ = self._setup(1)
        self.assertIsNone(mgr.handle("Stop", {"cwd": ""}))
        self.assertIsNone(mgr.handle("Stop", {"cwd": "/definitely/not/a/dir"}))
        self.assertEqual(len(s.q("select id from events where kind='stop_skipped'")), 2)

    def test_no_open_task_means_no_gate(self):
        mgr, _, cwd = self._setup(1)
        self.assertIsNone(mgr.handle("Stop", {"cwd": cwd}))

    def test_correction_is_captured_and_injected_next_session(self):
        mgr, _, cwd = self._setup(0)
        mgr.handle("UserPromptSubmit", {"cwd": cwd, "prompt": "Never use os.path, always use pathlib."})
        ctx = mgr.handle("SessionStart", {"cwd": cwd})
        self.assertIn("pathlib", ctx["hookSpecificOutput"]["additionalContext"])

    def test_pre_tool_asks_for_risky_command(self):
        mgr, _, cwd = self._setup(0)
        out = mgr.handle("PreToolUse", {"cwd": cwd, "tool_name": "Bash", "tool_input": {"command": "git reset --hard"}})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")


class RouterTests(unittest.TestCase):
    def test_scripted_default_never_claims_pass(self):
        out = Router({}).complete("verifier", "sys", "user")
        self.assertTrue(out.startswith("UNVERIFIED"))

    def test_openai_compatible_backend_roundtrip(self):
        class Fake(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(n))
                assert self.path == "/v1/chat/completions"
                assert body["model"] == "local-model"
                out = json.dumps({"choices": [{"message": {"content": "PASS looks right"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
            r = Router({"verifier": {"provider": "openai_compatible", "base_url": url, "model": "local-model"}})
            self.assertEqual(r.complete("verifier", "s", "u"), "PASS looks right")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
