# Lodestar (Claude Code plugin)

Adds persistent memory, an approval policy for risky shell commands, and a verification gate to Claude Code. A task is marked done only after the project's own checks pass.

Setup: install the Python package from the repository root with `python -m mgr.cli install --register`. Then use `/manager on` to start the local daemon, and `/manager status` or `/manager tasks` to inspect it. The daemon listens on 127.0.0.1 only.

The gate runs the first of: `.mgr.json` `verify` command, `package.json` test script, or a `tests/` folder. Without any of these, the task is marked done_unverified.
