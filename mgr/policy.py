"""Approval policy for risky shell commands. Matches trigger an 'ask' decision, not a silent block."""
import re

RULES = [
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r|Remove-Item\b.*-Recurse", re.I),
     "recursive delete"),
    (re.compile(r"git\s+push\b.*(--force|\s-f\b)", re.I), "force push"),
    (re.compile(r"git\s+reset\s+--hard|git\s+clean\s+-[a-zA-Z]*f", re.I), "discard local changes"),
    (re.compile(r"curl\b[^|]*\|\s*(sh|bash)\b|Invoke-WebRequest\b[^|]*\|\s*iex", re.I), "remote script execution"),
    (re.compile(r"\bDROP\s+(TABLE|DATABASE)\b", re.I), "database drop"),
]
SHELL_TOOLS = ("Bash", "PowerShell")


def classify(tool_name, tool_input):
    """Return a human-readable risk label, or None if the call is allowed without asking."""
    if tool_name not in SHELL_TOOLS:
        return None
    command = str((tool_input or {}).get("command", ""))
    for rx, label in RULES:
        if rx.search(command):
            return label
    return None
