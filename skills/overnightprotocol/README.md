# overnightprotocol

> **This repo now hosts the successor skill.** Up to **v0.1.3** it was `overnight-protocol`,
> a *single-pass* overnight mode (work the list once, stop by morning). From **v0.2.x** it is
> **`overnightprotocol`** — a **non-stopping loop** that owns all overnight use: it works your
> task list, then generates its own work, forever, until you explicitly stop it. The skill
> folder is now `overnightprotocol/` (no hyphen); the GitHub repo keeps its original name.

Autonomous, **never-stopping** overnight build loop for
[Claude Code](https://claude.com/claude-code).

You hand Claude a mission (or nothing at all) and walk away. This skill governs the
unattended session that keeps building — through your task list first, then through work
it finds itself: QA-hardening, auditing and fixing, then researching and shipping
quality-of-life improvements, in a permanent cycle. It pauses (never stops) at the usage
cap, survives compaction, crashes, and multi-day runs, and the **only** way it ends is
you telling it to.

It is a [Claude Code Agent Skill](https://code.claude.com/docs/en/skills): a `SKILL.md`
protocol plus guardrail scripts, hooks, and a usage daemon that make an unattended,
open-ended run survivable.

## What it does

- **Never stops on its own.** A `Stop`-hook watchdog blocks premature session ends; when
  the official mission is exhausted the **work-generation ladder** takes over (QA-harden →
  `/audit` + fix → research + build QoL → repeat), with anti-churn rules so "never stop"
  never degrades into thrashing. The off-switch is one press of the in-app Stop button or
  typing `END OVERNIGHT LOOP`.
- **Knows your REAL usage, not a guess.** The guardrail reads true percentages, best
  source first: the official usage API (the same data as the app's usage popup —
  refreshed every ~60s, including per-model weekly windows), the terminal status line, or
  a model-weighted **cost-based** estimate as last resort (raw token counts are
  model-blind and once under-read an Opus-heavy window by 35 points — that bug is fixed
  and regression-tested). Default threshold **95%**; estimates pause earlier at 85%.
- **Assumes you are gone.** Launching the loop means nobody is at the desk: all questions
  are batched into a single ~4-minute **question window** at kickoff (each with the
  assumption adopted on silence). After that, asking is forbidden — direction-ambiguous
  work is **parked** rather than guessed at or asked about, and questions can only return
  when the loop is genuinely out of buildable work.
- **Never leaves the repo broken.** Every completed item ends verified and committed on a
  dedicated `overnight-loop/<date>` branch; a run that dies at 4am still leaves a useful,
  committable state.
- **Survives everything.** A `SessionStart` hook re-injects the plan, report tail, and
  git log after compaction, resume, or a fresh session in an armed project; a heartbeated
  flag file lets crashed runs stand down automatically after ~30 minutes.
- **Shows it's alive.** A loop-aware status line (spinner, colour-coded usage bar, cycle
  counts), an in-app Stop button, periodic heartbeat cards, an optional tmux cockpit
  dashboard — and a rolling `OVERNIGHT_LOOP_REPORT.md` maintained live, so the morning
  handover always exists.

## Install

1. Download `overnightprotocol-<version>.zip` from the
   [latest release](../../releases/latest) and unzip it into your skills directory:
   ```bash
   unzip overnightprotocol-*.zip -d ~/.claude/skills/
   ```
2. Wire the guardrail scripts and hooks into Claude Code (one command; it copies the
   helpers to a stable path and merges the statusline + hooks into
   `~/.claude/settings.json`, backing up first), then **restart Claude Code**:
   ```bash
   bash ~/.claude/skills/overnightprotocol/scripts/install.sh
   ```
3. **Recommended — enable the real-usage source** (true percentages every ~60s even when
   the loop runs in the Claude Desktop app): install the terminal CLI
   (`npm install -g @anthropic-ai/claude-code`), run `claude` once and log in, then run
   `bash ~/.claude/overnight-loop/usage_daemon.sh --status` and (macOS) click
   **"Always Allow"** on the Keychain prompt. It should then report `real-api: available`.
4. Read `references/launch-guide.md` for the rest of the one-time machine setup —
   permissions (`--dangerously-skip-permissions` plus `permissions.deny` rails), Full
   Disk Access, and keeping the Mac awake (`caffeinate` + `tmux`).

**Upgrading from `overnight-protocol` ≤ v0.1.3 (the single-pass skill):** delete the old
skill folder (`rm -rf ~/.claude/skills/overnight-protocol`) and its installed runtime
(`rm -rf ~/.claude/overnight`), and remove its two hooks from `~/.claude/settings.json`
(the `Stop`/`SessionStart` entries pointing at `~/.claude/overnight/`). This skill
replaces all of it.

**Requires:** macOS (Linux works for everything except the Keychain path — credentials
are read from `~/.claude/.credentials.json` there), `jq`, `python3`, and Claude Code.

## Launching a loop

From your project directory — in the Claude Desktop app, or in a caffeinated `tmux`
terminal session:

```bash
caffeinate -is tmux new -s overnight     # terminal route; app route: just open the project
claude --dangerously-skip-permissions
```

Then a kickoff like:

> Overnight loop. Mission: [your list, or leave it out]. Keep building until I stop you.

The skill runs a preflight doctor, opens the **question window** (~4 minutes — its one
chance to ask you anything), branches, captures a baseline, arms the watchdog, starts
the usage daemon, and enters the loop. To end it: press the Stop button it renders, or
type **`END OVERNIGHT LOOP`**.

## Safety

Unattended runs are branch-only: never a force-push, history rewrite, or touch to
`main`; no deploys, publishes, or paid signups; no printing or moving of secrets; a
dirty working tree is listed at kickoff and anything secret-shaped is excluded before
the snapshot commit. Direction-ambiguous work is parked, not guessed at — hours of
building in the wrong direction is treated as worse than building nothing. The hard
limits belong in `permissions.deny` (enforced by Claude Code, not just the model) — see
the launch guide.

## Layout

| Path | What it is |
|---|---|
| `SKILL.md` | The loop protocol Claude follows. |
| `scripts/install.sh` | One-time installer — copies helpers, wires hooks + statusline. |
| `scripts/preflight.sh` | Pre-run doctor: deps, hooks, permissions, usage sources, git, keep-awake. |
| `scripts/check_usage.sh` | The usage guardrail — one machine-readable state line. |
| `scripts/usage_daemon.sh` | Background daemon — real-api / Desktop-cache / cost-estimate usage snapshots + flag heartbeat. |
| `scripts/statusline_usage_writer.sh` | Snapshots the status line's official rate-limit data (terminal sessions). |
| `scripts/usage_render.sh` | Shared renderer: loop-aware status line, kickoff banner, cockpit frames. |
| `scripts/cockpit.sh` | Optional live tmux dashboard. |
| `scripts/feeder.sh` | Standalone harvester for the (legacy) Desktop-app usage cache. |
| `hooks/overnightloop_stop.sh` | Watchdog — blocks a premature session end while the loop is armed. |
| `hooks/overnightloop_session_start.sh` | Recovery — re-injects loop state after compaction/resume/restart. |
| `references/launch-guide.md` | One-time machine setup, real-api setup, launch/stop/resume. |
| `references/templates.md` | The `OVERNIGHT_LOOP_PLAN.md` / `OVERNIGHT_LOOP_REPORT.md` skeletons. |
| `tests/` | Offline regression suites for the estimator and the real-api source (fake ccusage + mock endpoint). |

## License

[MIT](LICENSE) © 2026 robogears
