# overnightprotocol v0.2.2

This repo's skill has a new identity: **`overnightprotocol`**, a **non-stopping
autonomous build loop** — the successor to the single-pass `overnight-protocol` (≤
v0.1.3) previously published here. You give it a mission (or nothing at all) and walk
away; it works the list, then generates its own work — QA-harden → audit + fix →
research + build QoL improvements — in a permanent cycle, pausing (never stopping) at
the usage cap, until you explicitly end it. The old single-pass skill is retired; this
skill owns all overnight use. (v0.1.4–v0.2.1 were local iterations; v0.2.2 is the first
published loop release.)

## Highlights

- **The loop.** A `Stop`-hook watchdog blocks premature session ends while the loop flag
  is armed; the **work-generation ladder** keeps the run productive after the official
  mission is exhausted, with an explicit anti-churn bar (value-bar one-liners, no
  cosmetic reversals, depth over breadth) so "never stop" never becomes thrashing.
  Off-switch: a clickable in-app Stop button, or typing `END OVERNIGHT LOOP`. A
  heartbeated flag file lets crashed runs stand down automatically (~30 min), so a
  leftover flag can never wedge tomorrow's sessions.

- **Real usage percentages — the guardrail no longer guesses.** Sources, best first:
  - **`real-api`** *(new)*: the official usage endpoint (the same data as the app's
    usage popup), fetched by the background daemon every ~60s with the terminal CLI's
    own credentials (macOS Keychain after a one-time "Always Allow", or
    `~/.claude/.credentials.json` on Linux). Covers the 5-hour window **and every
    weekly window including per-model ones**; the weekly guardrail binds to the worst
    of them. Tokens are never logged, never persisted, never refreshed/rotated.
  - **Status line** (terminal sessions): unchanged, still authoritative when fresh.
  - **Cost-weighted estimate** *(rebuilt)*: the ccusage fallback now converts the
    active block's model-weighted **cost** rather than raw output tokens. Raw token
    counts are model-blind — an Opus-heavy window once read **44% when real usage was
    79%**, which would have blown straight through the cap. Cost tracks the limit in
    both directions (it also doesn't false-pause cheap Fable-heavy work), calibrates
    itself against every real reading, and rounds up.
- **Threshold restored to 95%** (from the estimate-era 80%): with real numbers the loop
  safely runs close to the cap. Estimate readings automatically pause 10 points
  earlier (85%), and `check_usage.sh` remains source-aware — a guess can never wear a
  real reading's authority.

- **The user is GONE — question protocol.** Launching the loop now *means* nobody is at
  the desk. All questions are batched into a single **~4-minute question window** at
  kickoff, each paired with the assumption adopted on silence. After the window:
  asking is forbidden; direction-ambiguous items are **parked** (plan section
  `Parked — needs direction`, mirrored to the report's `Questions for you`) instead of
  guessed at or asked about — and questions may only return at the **end of the rope**,
  when the mission is exhausted, a full ladder cycle cleared nothing, and everything
  left is parked.

- **Presence.** Loop-aware status line (proof-of-life spinner, colour-coded usage bar,
  cycle/done counts), kickoff banner, in-app Stop button, periodic heartbeat cards, an
  optional tmux cockpit, and a rolling `OVERNIGHT_LOOP_REPORT.md` maintained live — the
  morning handover always exists, even mid-run.

- **Tests.** A new offline regression suite (`tests/`, 27 checks) drives both the
  estimator and the real-api source against a fake ccusage and a mock usage endpoint:
  Opus-heavy pauses, cheap windows don't false-pause, expired/missing credentials fall
  through cleanly, per-model weekly windows can bind, fresh real snapshots can't be
  clobbered by estimates, and the default 95% threshold holds in both directions.

## Install

```bash
unzip overnightprotocol-*.zip -d ~/.claude/skills/
bash ~/.claude/skills/overnightprotocol/scripts/install.sh   # then restart Claude Code
```

Recommended one-time step for true percentages in Desktop-app runs: install + log in
the terminal CLI, then `bash ~/.claude/overnight-loop/usage_daemon.sh --status` and
(macOS) click **"Always Allow"** on the Keychain prompt. Full setup, launch, and stop
instructions: `references/launch-guide.md`.

**Upgrading from `overnight-protocol` ≤ v0.1.3:** delete
`~/.claude/skills/overnight-protocol` and `~/.claude/overnight`, and remove that
skill's two `~/.claude/overnight/…` hook entries from `~/.claude/settings.json` — this
skill replaces all of it.
