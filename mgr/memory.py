"""Bitemporal memory: facts are invalidated (valid_to set), never deleted."""
import re
import time

from .store import Store

_WORD = re.compile(r"[a-z0-9_]+")
_STOP = {
    "the", "a", "an", "to", "of", "and", "or", "in", "on", "is", "it", "for",
    "with", "this", "that", "be", "i", "you", "my", "we", "at", "as", "by", "so", "if", "then",
}
STANDING_KINDS = ("correction", "procedure", "preference")


def tokens(text):
    return {w for w in _WORD.findall((text or "").lower()) if len(w) > 1 and w not in _STOP}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class Memory:
    def __init__(self, store: Store):
        self.s = store

    def add(self, content, *, scope="project", project=None, kind="fact", key=None, source="manual"):
        """Store a memory. Returns (id, created_new). Same key in same scope supersedes the old entry."""
        content = content.strip()
        now = time.time()
        dup = self.s.q(
            "select id from memories where valid_to is null and scope=? and coalesce(project,'')=? and content=?",
            (scope, project or "", content),
        )
        if dup:
            return dup[0]["id"], False
        mid = self.s.x(
            "insert into memories(scope, project, kind, key, content, created, valid_from, source) "
            "values(?,?,?,?,?,?,?,?)",
            (scope, project, kind, key, content, now, now, source),
        )
        if key:
            olds = self.s.q(
                "select id from memories where valid_to is null and scope=? and coalesce(project,'')=? "
                "and key=? and id<>?",
                (scope, project or "", key, mid),
            )
            for old in olds:
                self.s.x("update memories set valid_to=?, superseded_by=? where id=?", (now, mid, old["id"]))
        return mid, True

    def forget(self, mid):
        self.s.x("update memories set valid_to=? where id=? and valid_to is null", (time.time(), mid))

    def active(self, project=None):
        return self.s.q(
            "select * from memories where valid_to is null and "
            "(scope='user' or (scope='project' and coalesce(project,'')=?)) order by created desc",
            (project or "",),
        )

    def retrieve(self, project, query="", budget_chars=1200, limit=8):
        """Rank active memories for a query. Standing rules (corrections, procedures, preferences)
        are always candidates; facts need token overlap when a query is given."""
        qt = tokens(query)
        scored = []
        for m in self.active(project):
            sim = jaccard(qt, tokens(m["content"]))
            standing = m["kind"] in STANDING_KINDS
            if qt and sim == 0 and not standing:
                continue
            scored.append((sim + (0.3 if standing else 0.0), m))
        scored.sort(key=lambda x: (-x[0], -x[1]["created"]))
        out, used = [], 0
        for _, m in scored:
            if len(out) >= limit:
                break
            cost = len(m["content"]) + 16
            if used + cost > budget_chars:
                break
            out.append(m)
            used += cost
        return out

    @staticmethod
    def format(mems):
        return "\n".join(f"- ({m['kind']}) {m['content']}" for m in mems)
