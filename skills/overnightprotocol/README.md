# overnightprotocol

> Up to **v0.1.3** this repo was `overnight-protocol`, a *single-pass* overnight mode. From
> **v0.2.x** it is **`overnightprotocol`** — a **non-stopping loop** that owns all overnight
> use. **v0.3.x** runs on Claude Code's built-in `/loop`: no hooks, no install, no usage
> daemon.

Autonomous, **never-stopping** overnight build loop for
[Claude Code](https://claude.com/claude-code).

You hand Claude a mission (or nothing at all) and walk away. This skill governs the
unattended session that keeps building — through your task list first, then through work
it finds itself: QA-hardening, auditing and fixing, then researching and shipping
quality-of-life improvements, in a permanent cycle. The **only** way it ends is you
telling it to.

It is a [Claude Code Agent Skill](https://code.claude.com/docs/en/skills): a `SKILL.md`
protocol plus four small helper scripts. The loop engine is Claude Code's own `/loop`
(dynamic mode: every turn schedules the next). Usage limits are left to Claude Code's
auto-resume. In the Desktop app that covers **5-hour limits only**, while the loop's chat
is open on screen; see the launch guide.

## Why not just `/loop`?

`/loop` is the engine; this skill is the driver. Plain `/loop` repeats a prompt and is
told to stop once the task looks complete. The overnight protocol adds what an unattended
multi-hour run needs:

- **Assumes you are gone.** All questions are asked in one kickoff message, each with the
  default it will use, during a ~4-minute window. After that it asks nothing; forks in
  direction are **parked**, not guessed.
- **Never runs out of work.** When your list is done, the **work-generation ladder** takes
  over (QA-harden → `/audit` + fix → research + build QoL → repeat), with anti-churn rules
  and re-audits only after real progress.
- **Never leaves the repo broken.** Every item ends verified, committed and pushed as a
  backup — on your current feature branch or a new `overnight-loop/<date-time>` branch (it
  asks which at kickoff; it never commits to `main`).
- **Guards against the usual overnight failures:**
  - new secret-shaped and oversized files are never committed (`stage-safe.sh`);
  - the common forms of force-push, push-to-main and branch deletion are blocked by deny
    rules (`deny-rules.sh`), on top of the instructions;
  - text from GitHub issues and the web is treated as data, not instructions.
- **Keeps its state on disk.** The plan's header records where it is (rung, cycle,
  in-progress item, background work). Supporting files live in a committed
  `.overnight-loop/` folder; private ones (audit reports, the deny-rule record) live inside
  `.git`, where they're never pushed. Every turn — timer tick, your message, a task notification, a
  usage-limit resume — re-reads it, so compaction and crashes don't lose the thread.
  "Resume the overnight loop" restarts a dead session.
- **Easy to stop.** An in-app Stop button, or type `END OVERNIGHT LOOP`.

## Install

1. If an older version is installed, move `~/.claude/skills/overnightprotocol` to the
   Trash first, so no retired files are left behind.
2. Unzip into your skills directory:

   ```bash
   unzip overnightprotocol-*.zip -d ~/.claude/skills/
   ```

That's it — nothing to wire up.

**Upgrading from v0.2.x (hooks + usage daemon):** end any old loop that's still running,
preview the cleanup, run it, then restart Claude Code:

```bash
bash ~/.claude/skills/overnightprotocol/scripts/uninstall-legacy.sh --dry-run
```

```bash
bash ~/.claude/skills/overnightprotocol/scripts/uninstall-legacy.sh
```

It removes the old hooks and status-line wiring from `~/.claude/settings.json` (backing up
first, keeping everything else), and moves the old runtime to the Trash.

**Upgrading from `overnight-protocol` ≤ v0.1.3:** delete
`~/.claude/skills/overnight-protocol` and `~/.claude/overnight`, and remove that skill's two
`~/.claude/overnight/…` hook entries from `~/.claude/settings.json`.

Then read `references/launch-guide.md` for the one-time machine setup: how usage limits
resume on Desktop vs terminal, permissions, and keeping the Mac awake.

**Requires:**
- a current Claude Code with `/loop` and usage-limit auto-resume (verified on Claude Code
  2.1.274 and 2.1.281, Desktop 2.9939), signed in with a Claude subscription — auto-resume
  isn't available on API-key billing;
- `git`;
- `python3` (Xcode Command Line Tools on macOS).

It's built and tested on macOS; the scripts avoid macOS-only commands where they can, and
the Desktop-app parts are macOS/Windows only.

## Launching a loop

From your project, in the Claude Desktop app or a terminal session:

> Overnight loop. Mission: [your list, or leave it out]. Keep building until I stop you.

The skill runs a preflight doctor, sends one kickoff message (questions with defaults, plus
the Stop button), branches, captures a baseline, sets its hard limits, and starts `/loop`.
To end it: press the Stop button, or type **`END OVERNIGHT LOOP`**.

## Safety

Unattended runs work on a branch only:
- no force pushes, history rewrites, or pushes to `main` or the base branch — the
  instructions forbid them, and deny rules block their common forms;
- no deploys, publishes or paid signups;
- no unattended CI, auth or release-config changes;
- new secret-shaped files are never committed (already-tracked ones are flagged), and
  anything skipped is listed in the report.

Direction-ambiguous work is parked, not guessed at.

## Layout

| Path | What it is |
|---|---|
| `SKILL.md` | The loop protocol Claude follows. |
| `scripts/preflight.sh` | Read-only pre-run doctor: `/loop` enabled, usage-limit resume per route, settings files, permission ask-rules, v0.2.x leftovers, git + unattended push, keep-awake. |
| `scripts/stage-safe.sh` | Stages the loop's work, skipping secret-shaped and oversized new files. |
| `scripts/deny-rules.sh` | Adds / removes the loop's hard limits (deny rules), recording exactly what it added. |
| `scripts/uninstall-legacy.sh` | One-time removal of the v0.2.x hooks / status line / usage daemon. |
| `scripts/statusline-everyday.sh` | The everyday `model \| 5h wk ctx` status line the cleanup keeps for you. |
| `references/launch-guide.md` | One-time machine setup, launch, stop, resume. |
| `references/templates.md` | The `OVERNIGHT_LOOP_PLAN.md` / `OVERNIGHT_LOOP_REPORT.md` skeletons. |
| `tests/` | Offline test suites for every script (throwaway folders only): `bash tests/run_all.sh`. |

## License

[MIT](LICENSE) © 2026 robogears
