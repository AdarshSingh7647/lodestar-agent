"""Manager core: turns Claude Code hook events into tasks, memory, verification and decisions."""
import json
import re
import time
from pathlib import Path

from . import policy, verifier
from .memory import Memory, jaccard, tokens
from .router import Router
from .store import Store

CORRECTION = re.compile(
    r"\b(don'?t|do not|never|always|instead of|stop using|prefer|from now on|remember that)\b", re.I)
# Only explicit standing-rule phrasing becomes persistent memory. "do not" alone is usually a one-off instruction.
STANDING_RULE = re.compile(
    r"\b(never|always|instead of|stop using|prefer|from now on|remember that)\b", re.I)
TASK_VERB = re.compile(
    r"\b(fix|add|implement|build|write|refactor|update|create|make|change|run|debug|test|rename|migrate|delete|remove)\b",
    re.I)
MATCH_THRESHOLD = 0.25
SHORT_PROMPT_TOKENS = 3
VERIFIER_SYSTEM = ("You are an independent verifier. Reply with PASS or FAIL on the first line, "
                   "then one sentence of reason.")


def _ctx(event, text):
    if not text:
        return None
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def _trim(obj, limit=500):
    s = json.dumps(obj, default=str)
    return s if len(s) <= limit else s[:limit] + "..."


class Manager:
    def __init__(self, store: Store, router: Router = None):
        self.s = store
        self.mem = Memory(store)
        self.router = router or Router({})

    # ---------- hook dispatch ----------
    def handle(self, event, payload):
        payload = payload or {}
        fn = {
            "SessionStart": self._session_start,
            "UserPromptSubmit": self._prompt,
            "PreToolUse": self._pre_tool,
            "PostToolUse": self._post_tool,
            "Stop": self._stop,
        }.get(event)
        if fn is None:
            self.s.event("hook_ignored", {"event": event})
            return None
        return fn(payload)

    # ---------- tasks ----------
    def open_tasks(self, cwd):
        return self.s.q("select * from tasks where cwd=? and status='open' order by updated desc", (cwd,))

    def match_or_create_task(self, cwd, prompt):
        now = time.time()
        open_ = self.open_tasks(cwd)
        qt = tokens(prompt)
        if open_ and len(qt) <= SHORT_PROMPT_TOKENS:
            t, how = open_[0], "resumed"
        else:
            best, score = None, 0.0
            for cand in open_:
                s = jaccard(qt, tokens(cand["title"]))
                if s > score:
                    best, score = cand, s
            if best is not None and score >= MATCH_THRESHOLD:
                t, how = best, "resumed"
            else:
                tid = self.s.x(
                    "insert into tasks(cwd, title, status, created, updated) values(?,?,?,?,?)",
                    (cwd, prompt.strip()[:200], "open", now, now))
                t, how = None, "created"
                t = self.s.q("select * from tasks where id=?", (tid,))[0]
        self.s.x("update tasks set updated=? where id=?", (now, t["id"]))
        return self.s.q("select * from tasks where id=?", (t["id"],))[0], how

    def _finish(self, tid, status, result):
        self.s.x("update tasks set status=?, last_result=?, updated=? where id=?",
                 (status, result, time.time(), tid))
        self.s.event("task_finished", {"status": status, "result": result}, task_id=tid)

    # ---------- events ----------
    def _session_start(self, p):
        cwd = p.get("cwd", "")
        self.s.event("session_start", {"cwd": cwd, "source": p.get("source")})
        parts = []
        mems = self.mem.retrieve(cwd)
        if mems:
            parts.append("Smart Agent Manager memory for this project:\n" + Memory.format(mems))
        open_ = self.open_tasks(cwd)
        if open_:
            t = open_[0]
            parts.append(f"Open task #{t['id']}: {t['title']} (verification round "
                         f"{t['rounds']}/{t['max_rounds']}). Do not treat it as done until the verifier passes.")
        return _ctx("SessionStart", "\n\n".join(parts))

    def _prompt(self, p):
        cwd, prompt = p.get("cwd", ""), p.get("prompt", "") or ""
        if prompt.lstrip().startswith("<task-notification>"):
            # System-generated background-task events are not user intent: never open or resume tasks from them.
            self.s.event("notification_ignored", {"text": prompt[:200]})
            return None
        is_standing_rule = bool(CORRECTION.search(prompt))
        if is_standing_rule:
            # Store only the sentence(s) that carry an explicit standing rule, not the whole task prompt.
            rule = " ".join(s for s in re.split(r"(?<=[.!?])\s+", prompt) if STANDING_RULE.search(s))
            if rule:
                mid, new = self.mem.add(rule[:500], scope="project", project=cwd,
                                        kind="correction", source="user_prompt")
                self.s.event("memory_captured", {"id": mid, "new": new, "text": rule[:200]})
            if not TASK_VERB.search(prompt):
                # Pure preference or correction: remember it, do not open a verifiable task.
                text = "Smart Agent Manager: remembered this as a standing rule for this project."
                return _ctx("UserPromptSubmit", text)
        task, how = self.match_or_create_task(cwd, prompt)
        self.s.event("prompt", {"task": task["id"], "how": how, "text": prompt[:300]}, task_id=task["id"])
        text = (f"Smart Agent Manager: task #{task['id']} ({how}), verification round "
                f"{task['rounds']}/{task['max_rounds']}. The task is complete only when the verifier passes.")
        mems = self.mem.retrieve(cwd, prompt, limit=5, budget_chars=800)
        if mems:
            text += "\nRelevant memory:\n" + Memory.format(mems)
        return _ctx("UserPromptSubmit", text)

    def _pre_tool(self, p):
        label = policy.classify(p.get("tool_name"), p.get("tool_input"))
        if not label:
            return None
        self.s.event("approval_required", {"tool": p.get("tool_name"), "rule": label,
                                           "input": _trim(p.get("tool_input"))})
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"Smart Agent Manager policy: {label}. Confirm this is intended.",
        }}

    def _post_tool(self, p):
        self.s.event("tool_post", {"tool": p.get("tool_name"), "input": _trim(p.get("tool_input"), 300)})
        return None

    def _stop(self, p):
        cwd = p.get("cwd", "")
        if not cwd or not Path(cwd).is_dir():
            self.s.event("stop_skipped", {"reason": "no valid cwd in hook payload", "cwd": cwd})
            return None
        open_ = self.open_tasks(cwd)
        if not open_:
            return None
        t = open_[0]
        tid, max_rounds = t["id"], t["max_rounds"]
        if t["rounds"] >= max_rounds:
            self._finish(tid, "needs_human", "verification budget exhausted")
            return {"systemMessage": f"Smart Agent Manager: task #{tid} still fails verification after "
                                     f"{max_rounds} rounds. Stopping for human review."}

        res = verifier.run(cwd)
        self.s.event("verify", {"checked": res["checked"], "passed": res["passed"],
                                "command": res["command"], "returncode": res["returncode"],
                                "output": res["output"][-800:]}, task_id=tid)

        if not res["checked"]:
            verdict = self.router.complete(
                "verifier", VERIFIER_SYSTEM,
                f"Task: {t['title']}\nAgent's last message:\n{(p.get('last_assistant_message') or '')[:4000]}")
            if verdict.strip().upper().startswith("PASS"):
                self._finish(tid, "done", "passed by model verifier (no automated check)")
                return {"systemMessage": f"Smart Agent Manager: task #{tid} passed model verification "
                                         "(no automated checks configured)."}
            self._finish(tid, "done_unverified", "no automated check and model did not pass")
            return {"systemMessage": f"Smart Agent Manager: no automated verification for task #{tid}; "
                                     "marked done_unverified. Add .mgr.json with a 'verify' command."}

        if res["passed"]:
            self._finish(tid, "done", "verified: " + res["command"])
            return {"systemMessage": f"Smart Agent Manager: verification passed for task #{tid} "
                                     f"(`{res['command']}`)."}

        rounds = t["rounds"] + 1
        self.s.x("update tasks set rounds=?, last_result=?, updated=? where id=?",
                 (rounds, res["output"][-1500:], time.time(), tid))
        if rounds >= max_rounds:
            self._finish(tid, "needs_human", "verification failed on final round")
            return {"systemMessage": f"Smart Agent Manager: task #{tid} failed verification {rounds} times. "
                                     "Stopping for human review."}
        reason = (f"Smart Agent Manager verifier failed (round {rounds}/{max_rounds}). "
                  f"`{res['command']}` exited with code {res['returncode']}. "
                  f"Fix the failures, then finish again.\n\n{res['output'][-1500:]}")
        return {"decision": "block", "reason": reason}

    # ---------- questions (batch clarification) ----------
    def enqueue_questions(self, batch, questions):
        now = time.time()
        return [self.s.x("insert into questions(batch, text, options, created) values(?,?,?,?)",
                         (batch, q["text"], json.dumps(q.get("options", [])), now))
                for q in questions[:10]]

    def answer(self, qid, answer):
        row = self.s.q("select * from questions where id=?", (qid,))[0]
        self.s.x("update questions set answer=?, answered=? where id=?", (answer, time.time(), qid))
        self.mem.add(f"Q: {row['text']} A: {answer}", scope="user", kind="preference",
                     key="q:" + row["text"].strip().lower()[:80], source="question")

    # ---------- UI state ----------
    def state(self):
        return {
            "tasks": self.s.q("select * from tasks order by updated desc limit 50"),
            "memories": self.s.q("select id, scope, project, kind, key, content, created, superseded_by "
                                 "from memories where valid_to is null order by created desc limit 200"),
            "events": self.s.q("select id, ts, task_id, kind, payload from events order by id desc limit 100"),
            "questions": self.s.q("select * from questions order by id desc limit 50"),
        }
