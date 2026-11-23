# Pitch package (working name: Smart Agent Manager; final name pending)

Status: draft for hackathon submission. Items marked PENDING are not done yet.

## 1. Problem (short)

Coding agents forget between sessions, declare work finished without checking, and repeat mistakes they were already corrected on. Every correction is lost when the session ends, so the developer re-explains the same rules and re-checks the same false "done" claims.

Who feels it: any team that runs coding agents daily. The cost is time and rework, and the risk is shipping work that was never verified.

## 2. Solution (short)

A local manager layer that sits inside the agent's own hooks and adds three things:

1. **Persistent memory:** a correction ("always use pathlib") is stored once and injected into every later session in that project.
2. **A verification gate:** a task cannot finish while the project's own checks fail. The failing output goes back to the agent, up to a round budget, then the task goes to a human. The gate can also use an independent check outside the agent, for example Slurm's own accounting or `sinfo`.
3. **An approval policy:** risky shell commands (recursive delete, force push, hard reset) ask first.

It runs on the user's machine, stores no third-party credentials, and works with any model that speaks the Anthropic or OpenAI-compatible API, including a self-hosted vLLM model.

## 3. Evidence so far (honest)

- 26 automated tests pass, plus an end-to-end demo that drives the real hook path (`python demo/run_demo.py`).
- Real headless Claude Code runs against a self-hosted model (Muse Glimmer 30B, two H100s) with the plugin hooks active. The gate blocked a stop once on a real plan defect, the model fixed it, and the gate passed.
- A controlled trial on a planning task (one run per arm, so this is a signal, not a conclusion):

| | Baseline, no manager | Manager, gate on |
|---|---|---|
| Turns | 38 | 48 |
| Wall time | 413 s | 636 s |
| Total tokens | 2.64M | 4.17M |
| Plan defects found after the run | none | none after gate fix |
| Gate blocks | n/a | 1 (fixed in retry) |

The honest read: the manager did **not** beat the baseline on cost or speed on this task. Its value so far is catching a defect the worker would otherwise leave unchecked. The cost of that check is more tokens and time. We have not yet measured this on a task where the baseline actually fails.

## 4. Demo (PENDING)

- Live demo: planned at harness.adarsh.fun. **Not live yet.** The home-server tunnel for adarsh.fun is down (returns 530); the new subdomain needs its own tunnel. PENDING.
- Video: automated screen recording of the real dashboard UI and terminal sessions. PENDING.
- Code: public repository (github.com/AdarshSingh7647/lodestar-agent).

## 5. Business model

**Customer:** engineering teams that run coding agents daily and need results they can trust across people and sessions.

**Free:** the local tool and plugin, plus a self-hostable daemon. This drives adoption.

**Paid (team tier, per seat per month, price not validated):**
- Shared team memory and project rules, synced across members' machines.
- Verification history, dashboards and audit export for review.
- Remote access from another device through an end-to-end encrypted relay.
- Admin controls: policy templates and SSO.

**Cost structure:**
- **Model tokens:** paid by the customer through their own key, or served from a local model at no per-call cost. We do not resell model usage.
- **Hosting:** a relay and team sync only. Content is end-to-end encrypted, so the server stores and forwards ciphertext, which keeps storage and bandwidth low.
- **Support and security review:** the main real cost for a team product. Not yet estimated.

## 6. Scalability

- Per-user compute runs on the user's machine: tests, the agent and the memory store. Adding users does not add vendor-side model or test compute.
- Server cost grows with messages, not model calls. A stateless relay plus team sync scales horizontally.
- Any role can use a vLLM endpoint, so a team can run the whole system without per-token fees.

These are design claims. Load tests have not been run.

## 7. Impact

- The gate reduces false "done" claims. Measured only on a single planning task so far.
- Memory removes repeated corrections. The mechanism is tested; the effect on real repeat-mistake rates is not measured yet.
- Approval prompts guard the most damaging shell commands. The policy list is short.

## 8. Video pitch script (target 4 min, hard limit 5 min)

| Time | Scene | What is said / shown |
|---|---|---|
| 0:00-0:30 | Hook | "Your coding agent just said it's done. Is it?" Show a failing test next to an agent message saying "all fixed." |
| 0:30-1:15 | Problem | Agents forget corrections and mark work done without checking. The cost: re-explaining rules and reviewing false "done" claims. |
| 1:15-2:00 | Solution | Three parts: memory, verification gate, approval policy. Runs on your machine. |
| 2:00-3:15 | Live demo | Show the dashboard UI and a terminal session: a blocked stop with the real failing output, a passing rerun, and a rule injected into the next session. |
| 3:15-3:45 | Trial result | Show the table above. Say plainly that the manager cost more on this task and caught a defect the baseline left unchecked. |
| 3:45-4:10 | Business model | Free local tool; paid team tier for shared memory, audit and remote access. Customers bring their own keys or a local model. |
| 4:10-4:30 | Close | What it is not yet (other harnesses, remote link, learning from failures). Where to find the code. |

## 9. Team and ask (PENDING)

- Team: PENDING (names and roles).
- Ask: PENDING (what you want from the judges or from the hackathon).

## 10. Hackathon criteria mapping

- **Impact on industry (10):** a daily, costly problem for every agent user. The demo shows the mechanism; real rate data is still to be measured.
- **Scalability (10):** compute and model cost stay with the user; server cost grows with messages, not model calls. Load tests still to be run.
- **Business model (10):** named customer, free local tier, paid team tier, main cost lines identified. Price not validated.
- **Working demo (tie-breaker):** `python demo/run_demo.py` runs the real hook path. The live hosted demo is PENDING.

## 11. Open items before submission

1. Final name: Lodestar (chosen; renamable).
2. Publish the code: needs your go-ahead on a public repo and license.
3. Host the demo at harness.adarsh.fun: needs a new Cloudflare tunnel on the home server.
4. Record the video with automated screen capture.
5. Team names and the ask.
