# Launch guide — read this before the first loop run

The skill controls Claude's behavior *inside* the session. A few things *outside* the
session decide whether a multi-day loop survives, and only the user can set them up. Do
the one-time setup once; then each launch is two commands. **This loop does not stop on
its own — read "Stopping the loop" first so you always know how to end it.**

## One-time setup

**1. Install the helper scripts and wire the loop hooks (one command).**
The installer copies scripts + loop hooks to a stable path (`~/.claude/overnight-loop/`)
and merges the gated `Stop`/`SessionStart` loop hooks into `~/.claude/settings.json`
(preserving your existing keys and any unrelated hooks, backing up first,
validating the JSON). There is deliberately no PreCompact hook — Claude Code ignores its
context, so recovery rides on the SessionStart hook instead; the installer also cleans up
that retired hook from older versions.

```bash
brew install jq   # only if `which jq` finds nothing — jq ships with recent macOS at /usr/bin/jq
bash ~/.claude/skills/overnightprotocol/scripts/install.sh
```

The statusline snapshot feeds `check_usage.sh` real rate-limit data. The installer only
sets `statusLine` if you don't already have a custom one (it won't clobber ccstatusline
etc.). It also sets `env.BASH_MAX_TIMEOUT_MS` to 18300000 so the pause procedure's single
long sleep is allowed. **Hooks load when a session starts — restart Claude Code after
installing before you launch a loop.**

**Verify:** open Claude Code, send one message, then
`cat ~/.claude/overnight-usage.json` (percentages should appear) and
`bash ~/.claude/overnight-loop/preflight.sh "$PWD"` (should end with `monitor:ok`).

**Knowing the real number (the usage daemon).** The loop runs a small background daemon
(`usage_daemon.sh`, started in Phase 0) that refreshes the usage snapshot every ~3 minutes
so the guardrail is never blind — and heartbeats the loop flag so the hooks can tell a
live run from a crashed one. It uses the **real** status-line number when Claude Code
pushes it, and otherwise a **token-based estimate that self-calibrates** to the real number
the first time it sees one — and the checker reports estimates as `EST_*` with an earlier
pause margin, so a guess can never wear a real reading's authority. Check it any time with
`bash ~/.claude/overnight-loop/usage_daemon.sh --status` (look for `source: real`,
`real-app`, or `estimate-ccusage`; `unknown` — or `estimate-ccusage` with
`cc_calibrated: false` — means no real source has fed the calibration yet; the output
also reports whether the daemon/feeder processes are actually running).

**Ways to feed the REAL number (the daemon uses whichever is available, best first):**
1. **The `real-api` source — RECOMMENDED; works everywhere incl. Desktop-app loops, refreshes
   every ~60s.** One-time setup:
   ```bash
   npm install -g @anthropic-ai/claude-code   # the terminal CLI (separate from the Desktop app)
   claude                                     # log in once (browser OAuth), then /exit
   bash ~/.claude/overnight-loop/usage_daemon.sh --status   # triggers the Keychain prompt
   ```
   On that first `--status` run macOS asks to allow `security` to read the
   **"Claude Code-credentials"** Keychain item — click **"Always Allow"** (this is the grant
   that lets the unattended daemon authenticate). `--status` should then say
   `real-api: available (Keychain token, valid until …)`, and once a loop's daemon is
   running the snapshot shows `source: real-api` with the true 5h% and the WORST weekly
   window (including per-model weeklies). If the token expires mid-run (the CLI refreshes
   it whenever it runs; the daemon never refreshes tokens itself), the daemon falls back
   to the estimate and says why — running `claude` once in a terminal heals it.
2. **Run the loop in the terminal CLI** — the status line fires natively and writes the real
   number (`source: real`). Also keeps the Keychain token fresh for source 1.
3. **The Desktop-app cache** (`source: real-app`, via the daemon or `feeder.sh`) — **dead on
   current app versions** (the app stopped writing `plan-usage-history.json` ~July 2026);
   kept only for older installs.
Every real reading also **calibrates** the cost-weighted estimate, so even the fallback
tracks reality. Kill switch for the API source: `OVERNIGHT_REAL_API=off`.

**2. Permissions (the real "no questions" switch).**
One unanswered prompt stalls the loop. Options:

- `claude --dangerously-skip-permissions` — the most unattended mode. It still honors
  explicit `ask` rules, so run `/permissions` once and remove any leftover `ask` rules
  first. Encode the loop's hard limits as `permissions.deny` rules — using the **colon
  prefix syntax** (a space-star never matches): `Bash(git push --force:*)`,
  `Bash(git push -f:*)`, `Bash(git clean:*)`. Deny is enforced by Claude Code, not the
  model.
- Middle ground: `claude --permission-mode acceptEdits` plus a Bash allowlist for the
  loop's build/test/git commands (remember to allowlist `git push`).

**3. Full Disk Access** (System Settings → Privacy & Security → Full Disk Access) for your
terminal app, granted **before** the first run — a mid-run restart can otherwise revoke
the terminal's access to your project and every shell command fails with "Operation not
permitted."

**4. Keep the Mac awake — and keep the terminal open.**

```bash
caffeinate -is tmux new -s loop
```

- `-i` prevents idle sleep, `-s` keeps it awake **only on AC power — stay plugged in.**
- **Caveat that bites:** `caffeinate` here wraps the **tmux client**, so its keep-awake
  assertion lasts only while that client runs. **Closing or detaching the terminal
  window drops the assertion and the Mac can sleep.** For a true unattended multi-day
  loop, either leave the terminal window open, or start the keep-awake independently:
  `caffeinate -is -w $$ &` inside the tmux session, or run `caffeinate` in its own
  always-on window. Confirm it's live: `pmset -g assertions | grep -i PreventUserIdleSystemSleep`.
- **Lid stays OPEN** unless you have an external display + keyboard (clamshell); closing
  the lid sleeps the Mac regardless of `caffeinate`.

## Launching a loop

Inside the caffeinated tmux session, from the project directory:

```bash
claude --dangerously-skip-permissions
```

Then paste the kickoff, e.g.:

> Overnight-protocol loop. Tasks: [the list, or "none — just keep improving it"]. Keep
> building and improving non-stop until I stop you. I'm AFK.

The skill takes it from there: preflight, plan file, branch, baseline, then the work loop
and — when your list is done — the work-generation ladder (QA → /audit + fix → research +
QoL), forever.

## Watching it run (visuals)

While a loop is active you get three at-a-glance "it's alive" signals:

- **Status line** (bottom bar, refreshes ~30s) — automatic, no setup: a 🌙 + a spinner that
  *visibly turns* each refresh (proof of life), a colour-coded 5-hour usage bar, cycle/done
  counts, the current action, and `⌨ END OVERNIGHT LOOP`.
- **Heartbeat card in chat** every ~20 turns: `🌙 OVERNIGHT LOOP · ACTIVE — usage NN% …`.
- **Cockpit pane** (optional, tmux only) — a live dashboard: usage bars (5h / weekly / context),
  cycle · done · build, current + last action, uptime. It opens automatically in Phase 0 when
  you launch inside tmux; to open one by hand:
  ```bash
  tmux split-window -v -l 22% 'bash ~/.claude/overnight-loop/cockpit.sh'
  ```
  It closes itself when the loop ends.

Prefer the loop status in your **tmux status bar** instead of a pane? Add to `~/.tmux.conf`:
```tmux
set -g status-right '#(sh ~/.claude/overnight-loop/usage_render.sh statusline)'
set -g status-interval 15
```
When no loop is active this prints nothing, so your normal status bar is unaffected.

## Stopping the loop (important — it will not stop on its own)

The watchdog blocks a premature end while the loop is active. To end it, remove the flag:

```bash
rm ~/.claude/overnight-loop-active
```

The next time the session ends, it's allowed to — Claude will finalize
`OVERNIGHT_LOOP_REPORT.md`, push the branch, and stop. Removing the flag also stops the
background usage daemon within seconds (it polls the flag every 5s; worst case ~90s if a
refresh is mid-flight). One exception: the standalone **feeder** (`feeder.sh`) is NOT
gated on the flag — if you started one in a terminal, Ctrl-C it (or `tmux kill-session`)
yourself. You can also just tell Claude "stop the loop" in the session and it will delete
the flag, wrap up, and stop. A hard ceiling ("loop until 6am") set at kickoff stops it
automatically at that time (the watchdog blocks once for wrap-up, then releases).

## If the run died (resume)

Nothing is lost — the plan file, report, and commits persist. From the project directory:

```bash
claude --continue --dangerously-skip-permissions
```

Then: *"Resume the overnight loop: restart the usage daemon, re-read
OVERNIGHT_LOOP_PLAN.md and OVERNIGHT_LOOP_REPORT.md and continue — don't stop."*
(The `SessionStart` loop hook injects that state automatically only while the flag's
heartbeat is fresh — within ~30 min of the crash. After that the hooks treat the run as
dead and stand down, so the manual prompt above is the reliable path; restarting the
daemon re-arms the heartbeat and the watchdog with it.)

## Known limits — set expectations

- The 95% ceiling (default — safe to run this close to the cap because the guardrail reads
  REAL percentages via real-api/status line; ESTIMATE readings pause earlier, at 85%) is
  enforced *between checks*, not mid-token; one huge task started near the threshold can
  overshoot before the next check. The mid-task check mitigates it, and the remaining
  5-point buffer above the threshold is the crumple zone — check usage BEFORE any single
  large operation when the last reading was above ~85%.
- A weekly-cap hit means the loop **pauses until the weekly window resets** (which can be
  days) rather than stopping — the platform freezes the session anyway. If you'd rather it
  stop on a weekly cap, remove the flag.
- A never-stopping builder is only as safe as its reversibility: everything is on a branch,
  committed per increment, and pushed — review the branch before merging to main.
