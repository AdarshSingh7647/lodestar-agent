"""Model router. Each role (e.g. 'verifier') maps to a backend: scripted, OpenAI-compatible (vLLM, LiteLLM, OpenAI) or Anthropic."""
import json
import os
import urllib.request


class ScriptedModel:
    """Default when a role has no backend. Deterministic and never claims a pass."""

    def complete(self, system, user):
        return "UNVERIFIED: no model configured for this role."


def _post(url, body, headers, timeout):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class OpenAICompatible:
    def __init__(self, base_url, model, key_env=None, timeout=120):
        self.base_url, self.model, self.key_env, self.timeout = base_url, model, key_env, timeout

    def complete(self, system, user):
        headers = {}
        key = os.environ.get(self.key_env, "") if self.key_env else ""
        if key:
            headers["Authorization"] = f"Bearer {key}"
        body = {"model": self.model, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": user}]}
        data = _post(self.base_url.rstrip("/") + "/chat/completions", body, headers, self.timeout)
        return data["choices"][0]["message"]["content"]


class Anthropic:
    def __init__(self, model, base_url="https://api.anthropic.com", key_env="ANTHROPIC_API_KEY", timeout=120):
        self.model, self.base_url, self.key_env, self.timeout = model, base_url, key_env, timeout

    def complete(self, system, user):
        headers = {"x-api-key": os.environ.get(self.key_env, ""), "anthropic-version": "2023-06-01"}
        body = {"model": self.model, "max_tokens": 1024, "system": system,
                "messages": [{"role": "user", "content": user}]}
        data = _post(self.base_url.rstrip("/") + "/v1/messages", body, headers, self.timeout)
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


class Router:
    def __init__(self, roles=None):
        self.roles = roles or {}

    def backend(self, role):
        cfg = self.roles.get(role)
        if not cfg:
            return ScriptedModel()
        kind = cfg.get("provider")
        if kind == "openai_compatible":
            return OpenAICompatible(cfg["base_url"], cfg["model"], cfg.get("key_env"))
        if kind == "anthropic":
            return Anthropic(cfg["model"], cfg.get("base_url", "https://api.anthropic.com"),
                             cfg.get("key_env", "ANTHROPIC_API_KEY"))
        raise ValueError(f"unknown provider {kind!r} for role {role!r}")

    def complete(self, role, system, user):
        return self.backend(role).complete(system, user)
