---
name: overnightprotocol
version: 0.2.2
description: overnightprotocol (v0.2.2) — THE overnight protocol; a non-stopping autonomous build loop for Claude Code. Use whenever the user wants work done unattended while they are AFK or asleep — triggers include "overnight", "overnight protocol", "overnightprotocol", "overnight loop", "while I sleep", "run all night", "grind through this tonight", "keep building non-stop", "never stop building", "loop until I stop you", "continuously build and improve", or any request to build/fix a backlog without supervision. Works through the official task list, and when that is exhausted it generates its own work — QA-harden, run /audit and fix findings, then research the project and build QoL improvements — looping forever. Stays under the usage cap by PAUSING (never stopping — the guardrail reads REAL usage percentages via the real-api source or the terminal status line, with a cost-weighted estimate as last resort), keeps the repo always-committable, and stops ONLY when the user explicitly ends it (removing the loop flag). The old single-pass overnight-protocol skill is retired — this is the only overnight skill.
---

# Overnight Protocol — LOOP

This is the successor to the original single-pass overnight-protocol (retired 2026-07-21) with one deliberate change: **it never stops on its own.** The user has asked Claude to keep building and improving a project continuously and unattended. The job is to make forward progress forever — through their task list first, then through work it finds itself — until the human explicitly ends it. It is the tool for "just keep going all night / all weekend and make my app better."

Three rules override everything else:

1. **The user is GONE — never ask a question mid-run.** Whoever launches this loop is telling you they are about to walk away from the desk: nobody will answer, and one unanswered question stalls the entire night. So ALL questions are front-loaded into the **Phase 0 question window** (the first ~4 minutes, ONE batch, each question paired with the assumption you'll adopt if unanswered). Once that window closes, asking is **forbidden** — with exactly one exception, the **end-of-rope rule** (see Autonomy rules): you may ask again only when you are genuinely out of buildable work. Mid-run ambiguity is handled by deciding-and-logging (small calls) or the **parking rule** (big directional calls) — never by asking, and never by charging ahead on a big guess: hours of building in the wrong direction is WORSE than building nothing.
2. **Never leave the repo broken.** Every completed unit of work ends in a verified, committed state. Because this run can last days, "always safe to walk away" matters more here, not less.
3. **Never stop building — and never blow the usage cap.** These are not in tension: **pausing is not stopping.** When usage nears the cap you PAUSE (sleep) and RESUME after reset; the loop survives the pause and keeps building. A locked-out account cannot build, so the guardrail is what makes "never stop" possible. The ONLY thing that ends the loop is the human removing the flag file (the off-switch below).

**The off-switch — a one-press button in the app, a typed phrase everywhere.** The Stop-hook watchdog blocks a premature end, so give the user a clear way to stop:
- **In the Claude app/web:** render a clickable **Stop button** at kickoff (see "Stop button" under Presence & progress display) — the user just presses it.
- **Universal fallback (works anywhere, incl. a bare terminal):** typing, in bold:

> **TO STOP: press the Stop button, or type END OVERNIGHT LOOP**

When the user presses the button or types `END OVERNIGHT LOOP` (the button sends exactly that phrase) — or anything clearly meaning stop ("stop the loop", "end it") — do this: delete the flag (`rm ~/.claude/overnight-loop-active`), run Wrap-up (final commit + finalize `OVERNIGHT_LOOP_REPORT.md`), then stop. Removing the flag is what lets the watchdog allow the end.

**Dead-run failsafe (how a crashed loop stands down).** The usage daemon refreshes the flag file's timestamp every cycle as a heartbeat. If the flag goes untouched for **30+ minutes** (crash, reboot, force-quit), the hooks treat the run as dead and stand down automatically — a leftover flag can never wedge tomorrow's normal sessions. The flip side: if the daemon dies mid-run, the watchdog disarms ~30 min later, so recovery (Context resilience below) always restarts the daemon first.

**Presence & progress display — so the user always sees it's alive.** The loop paints itself onto several surfaces; keep them fed (all cheap, all optional-degrading):

- **Kickoff banner (once, at the very start):** run `sh ~/.claude/overnight-loop/usage_render.sh banner` — a small boxed "OVERNIGHT LOOP — ACTIVE · type END OVERNIGHT LOOP to end".
- **Stop button (Claude app/web only):** at kickoff, **if an interactive-widget tool is available** (`mcp__visualize__show_widget` — i.e. you're in the app, not a bare terminal), render a small stop-control panel the user can click. Its button sends `END OVERNIGHT LOOP`, so a press stops the loop exactly like typing it. Re-render it with the heartbeat so a fresh clickable button stays near the bottom. (The tool needs its `read_me` loaded once first.) Use this exact widget:
  ```html
  <div style="padding:1rem 0"><div style="background:var(--surface-2);border:0.5px solid var(--border);border-radius:12px;padding:1.25rem;max-width:420px">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px"><i class="ti ti-moon" style="font-size:20px;color:var(--text-secondary)" aria-hidden="true"></i><span style="font-size:16px;font-weight:500">Overnight loop</span><span style="margin-left:auto;font-size:12px;font-weight:500;background:var(--bg-success);color:var(--text-success);padding:3px 10px;border-radius:999px">active</span></div>
    <p style="font-size:14px;color:var(--text-secondary);margin:0 0 16px">Building and improving until you stop it.</p>
    <button onclick="sendPrompt('END OVERNIGHT LOOP')" aria-label="Stop the overnight loop" style="width:100%;background:var(--bg-danger);color:var(--text-danger);border:0.5px solid var(--border-danger);border-radius:var(--radius);padding:12px;font-size:15px;font-weight:500;display:flex;align-items:center;justify-content:center;gap:8px;cursor:pointer"><i class="ti ti-player-stop" style="font-size:18px" aria-hidden="true"></i> Stop the loop ↗</button>
    <p style="font-size:12px;color:var(--text-muted);margin:10px 2px 0;line-height:1.5">Sends "end overnight loop" — Claude finishes the current step, commits, writes the report, then stops.</p>
  </div></div>
  ```
  **In a bare terminal (no widget tool):** skip the widget entirely — the user just types `END OVERNIGHT LOOP` (the status line already carries that reminder). Don't create Desktop files or hotkeys.
- **Live status line (automatic, no action needed):** while the loop flag exists, the bundled statusline becomes loop-aware — a spinner that visibly turns each refresh (proof of life), a colour-coded 5-hour usage bar, cycle/done counts, the current action, and `⌨ END OVERNIGHT LOOP`. It updates itself every ~30s; you never print it.
- **Keep the status file current (one `printf` per transition)** so the status line and cockpit show the *real* current action and pause state. Write `~/.claude/overnight-loop-status` as `state|detail|reset_epoch` (`state` ∈ `working|paused|blocked|wrapup`):
  - start an item → `printf 'working|<short item name>|\n' > ~/.claude/overnight-loop-status`
  - pause for usage → `printf 'paused|resumes <HH:MM>|<reset_epoch>\n' > ~/.claude/overnight-loop-status`
  - wrap-up → `printf 'wrapup||\n' > ~/.claude/overnight-loop-status`  ·  at stop → `rm -f ~/.claude/overnight-loop-status`
- **Heartbeat card (deliberate exception to minimal narration): roughly every 20 turns** print this compact block and nothing else, with live values from `check_usage.sh` + the report:
  > **🌙 OVERNIGHT LOOP · ACTIVE** — usage **\<5h%>%** · cycle \<n> · \<N> done  
  > building \<current item> · **press the Stop button, or type END OVERNIGHT LOOP**
- **Cockpit (optional live dashboard):** if the user is in tmux and wants a dashboard pane, it was opened in Phase 0; otherwise skip it.

Bundled helpers (installed to `~/.claude/overnight-loop/` by `scripts/install.sh`): `check_usage.sh` (guardrail), `statusline_usage_writer.sh`, `preflight.sh`, `usage_daemon.sh`, `feeder.sh`, and the loop `overnightloop_stop.sh` / `overnightloop_session_start.sh` hooks. (There is deliberately no PreCompact hook — Claude Code ignores its `additionalContext`, so recovery rides on the SessionStart hook's `compact` matcher instead.) Templates for the plan/report live in `references/templates.md`; one-time machine setup is in `references/launch-guide.md`.

## Phase 0 — Preflight (do once, before any work)

1. **Run the doctor and act on it — the one moment a human is present.** Run `bash ~/.claude/overnight-loop/preflight.sh "$PWD"`. **If that file does not exist (first time on this machine), run the installer first — `bash <skill>/scripts/install.sh` — then run the installed preflight.** If preflight reports `monitor:blind` or any `FAIL`, say so loudly in your first response. **If you had to run `install.sh` in THIS session: the hooks it wired are INERT until Claude Code restarts (hooks are snapshotted at session start). Tell the user — who is still at the keyboard — to restart Claude Code and re-issue the kickoff, and do NOT arm the flag (step 7) in this session.** Record the monitor mode (`ok` / `estimate` / `blind`) in the plan header; if it stays `blind`, run conservatively (smaller units, check usage more often, pause earlier).
2. **The question window — the ONLY time you may ask anything (≈4 minutes).** Treat the user as walking away from the desk RIGHT NOW. If anything about the mission is unclear enough to change WHAT you would build — scope, priorities, a fork between two genuinely different designs, deploy targets — ask it here, as ONE numbered batch in your kickoff response, each question paired with the assumption you will adopt if unanswered ("if no answer: I'll do X"). Then wait out the window: a single `sleep 240` Bash call (pass an explicit timeout of 300000 ms — the default 2-min cap would kill it); a reply sent while you slept reaches you when the call returns. Answered → incorporate the answers. Silence → your stated assumptions ARE the direction now: copy them into the report's `Decisions & assumptions` and move on — **from this point questions are forbidden** (end-of-rope rule excepted). If you have NO questions, say "No questions — mission is unambiguous" and skip the sleep entirely; don't burn 4 minutes of ceremony.
3. **Record the start time.** `date +%s` → `START_EPOCH` (a literal integer in the plan header). There is **no** END_EPOCH by default — the loop is unbounded. If the user asked for a hard ceiling ("loop until 6am"), record it as `CEILING_EPOCH`; otherwise omit it.
4. **Branch first — then snapshot, crash-aware.** Confirm a git repo. **Create/switch to `overnight-loop/YYYY-MM-DD` BEFORE committing anything** (the snapshot must never land on main). Then, if the tree was **dirty**: print the full `git status --porcelain` list in your kickoff response so the user sees exactly what will be committed and PUSHED, and commit it as a labeled snapshot on the loop branch — **excluding secret-shaped files**:
   ```
   git add -A -- ':(exclude)*.pem' ':(exclude)*.key' ':(exclude)*.env*' ':(exclude)*credential*' ':(exclude)*token*' ':(exclude)*secret*'
   git commit -m "chore: pre-loop snapshot of uncommitted work"
   ```
   List anything excluded as EXCLUDED in the kickoff response (the user can commit it deliberately if it's a false positive). Never `git stash`.
   **Relaunch shortcut — only when the loop is genuinely still armed:** if an `OVERNIGHT_LOOP_PLAN.md` exists AND `~/.claude/overnight-loop-active` exists with a `cwd` matching this project, switch to the existing branch and jump to Context resilience recovery. If the plan exists but the flag does NOT (the leftover of any cleanly-stopped earlier run), this is a NEW run: extend or archive the old plan, and still execute steps 4–7 in full (files, baseline, flag, daemon). **Commit prefix:** every loop commit uses exactly `loop:` so `git log --grep='^loop'` finds all the work.
5. **Plan & report files — copy the skeletons, seed the mission.** Copy BOTH `OVERNIGHT_LOOP_PLAN.md` AND `OVERNIGHT_LOOP_REPORT.md` skeletons from `references/templates.md`, fill the plan header (including the fully-resolved `USAGE_CHECK` command — thresholds are TWO separate arguments, e.g. `… check_usage.sh 95 90`, never one quoted string; the default 5-hour threshold is **95%** — the guardrail reads REAL percentages (real-api / status line), so the loop can safely run close to the cap; ESTIMATE readings automatically pause 10 points earlier, at 85%), and add one checkbox per **official mission item** the user gave, each with a one-line acceptance note, ordered by dependency then value. If the user gave **no** mission, that is fine — the plan starts empty and you go straight to the ladder. Append a **`## Ladder backlog`** section (starts empty; the loop fills it).
6. **Baseline — save the verbatim output.** Run the existing suite / build / typecheck; paste the verbatim output into the report's `Baseline` block (the report file exists now — step 5 created it). If red at baseline, fix it as "item 0" if it looks under one timebox; otherwise record it, fall back to build/typecheck verification, and make repairing it the first ladder task.
7. **Arm the loop watchdog — atomically, and never over a live loop.** First check for an already-armed loop:
   ```
   [ -f ~/.claude/overnight-loop-active ] && jq -r '.cwd // "?"' ~/.claude/overnight-loop-active
   ```
   If a flag exists with a DIFFERENT cwd, another loop is armed — tell the user (they are still present) and let them decide; never silently overwrite it. Then write the flag atomically with `jq` (correct JSON even if the path contains quotes):
   ```
   jq -n --arg cwd "$PWD" --arg plan "$PWD/OVERNIGHT_LOOP_PLAN.md" \
     '{cwd:$cwd, plan:$plan, mode:"loop"}' > ~/.claude/overnight-loop-active.tmp \
   && mv ~/.claude/overnight-loop-active.tmp ~/.claude/overnight-loop-active
   ```
   **If the user set a hard ceiling**, use this variant instead — the field name must be exactly `end_epoch` (it is what the Stop hook reads):
   ```
   jq -n --arg cwd "$PWD" --arg plan "$PWD/OVERNIGHT_LOOP_PLAN.md" --argjson end <CEILING_EPOCH> \
     '{cwd:$cwd, plan:$plan, mode:"loop", end_epoch:$end}' > ~/.claude/overnight-loop-active.tmp \
   && mv ~/.claude/overnight-loop-active.tmp ~/.claude/overnight-loop-active
   ```
   Removing this file is the off-switch. (The daemon you start next heartbeats this file; see the dead-run failsafe above.)
8. **Start the usage daemon (so the guardrail knows the REAL number, refreshed every ~3 min).** Launch it detached — it keeps `~/.claude/overnight-usage.json` fresh whether or not the status line is ticking:
   ```
   nohup bash ~/.claude/overnight-loop/usage_daemon.sh --watch >/dev/null 2>&1 &
   ```
   It prefers the real status-line reading when present (authoritative) and otherwise writes a token-based estimate that **self-calibrates** to the real number the first time the status line feeds one — so the guardrail stops false-pausing at a fake 100%. It is single-instance, heartbeats the loop flag, and **exits on its own within seconds of the flag being removed** (it polls the flag every 5s; worst case ~90s if a refresh is mid-flight — the off-switch also stops the daemon). Sanity-check once with `bash ~/.claude/overnight-loop/usage_daemon.sh --status` (it also reports whether the daemon process is alive).
9. **Show it's alive (presence).** Print the kickoff banner once: `sh ~/.claude/overnight-loop/usage_render.sh banner`. Then, **only if this session is inside tmux** (`[ -n "$TMUX" ]`), open the live cockpit dashboard in a pane below: `tmux split-window -v -l 22% 'bash ~/.claude/overnight-loop/cockpit.sh'` (it repaints usage/cycle/action every few seconds and closes itself when the loop ends). If not in tmux, skip the cockpit — the loop-aware status line and the heartbeat card (see Presence & progress display) carry the presence signal. Initialise the status file: `printf 'starting||\n' > ~/.claude/overnight-loop-status`.

## Work loop (repeat per item)

1. Re-read `OVERNIGHT_LOOP_PLAN.md`; pick the top open item (official mission first, then `Ladder backlog`).
2. **Mark it in progress before touching code:** rewrite its checkbox to `- [~] N. <item> — STARTED <epoch> (attempt 1) — approach: <one line> — wip:none`.
3. Implement in small increments; run the relevant test/build after each increment.
4. When the acceptance note is met and nothing that passed at baseline is now failing: commit with `loop: <item>` (including plan + report updates), **push** if a remote exists. Flip the checkbox to `- [x] N. <item> — <duration>`, append decisions to the report.
5. Run the usage check (below). If it says continue, take the next item. **If the plan has no open items left, DO NOT STOP — enter the work-generation ladder.**

**Timebox per item:** ~45–60 min from the `[~]` start epoch unless it's a centerpiece. Blocked after two genuinely different attempts → mark `- [>] N. <item> — <reason>`, preserve the diagnostic diff as a committed `.overnight-loop/skipped-<item>.patch`, move on. **Environment-blocked** (needs a display / eyeball) → `- [!] N. <item>` with the exact morning command in the report; front-load such work early.

## The work-generation ladder (the heart of the loop)

When the official mission is exhausted — or there was none — you generate your own work. **Never idle, never stop.** Climb the rungs in order; each cycle, start again at the top because earlier rungs create new work for later ones.

**Rung 1 — QA-harden what exists.** Run the full suite, lint, typecheck. Fix flaky/failing tests, handle unhandled error/edge cases in touched code, clear real `TODO`/`FIXME` in files you've changed, tighten stale docs. Commit each fix `loop: qa — <what>`.

**Rung 2 — Audit and fix.** Run the `/audit` skill on this project (`/audit .`). Take its report and, for every **Safe** finding, apply the fix and commit `loop: audit-fix — <ID>`. For **Needs-verification** findings, do the non-drastic version and note the recommendation in the report (never do a drastic thing the user didn't ask for — schema rewrites, framework swaps → recommend, don't do). Add anything you couldn't safely auto-fix to `Ladder backlog`. If the audit comes back **clean/dry**, drop to Rung 3.

**Rung 3 — Research and improve (QoL).** With no bugs left to fix, make the app *better*:
- **Research the project:** re-read the README/docs, `git log` for direction, open issues/PRs (`gh issue list`, `gh pr list` if `gh` is authed), `TODO`/`FIXME` across the tree, the actual user-facing flows, and how comparable tools solve the same job.
- **Plan concrete QoL:** brainstorm a batch of specific, feelable improvements (real cancellation that cleans up, retry/backoff on flaky network, clearer error messages, progress feedback, sensible defaults, small UX wins, accessibility, docs a newcomer would thank you for). Add them to `Ladder backlog` as checkboxes with acceptance notes.
- **Build them** through the normal work loop.

**Rung 4 — Loop.** Go back to Rung 1: the code changed, so re-QA and re-audit. Continue forever.

**Anti-churn — so "never stop" never degrades into thrashing (mandatory):**
- Every self-directed change must clear a **value bar**: write a one-line "this helps because <X>" in the decisions log before building it. If you can't, don't build it.
- **Never undo good work to have something to do.** No cosmetic reversals, no re-formatting churn, no rewriting working code for taste.
- If a whole ladder cycle produced only trivial/cosmetic changes, **widen the research scope** (a new subsystem, a real feature gap, test coverage) rather than making smaller noise.
- Prefer **depth** (finish and polish a real improvement) over breadth (many half-things). Keep the repo always-committable and every change independently revertible.
- **Parked-for-direction items are not rungs.** Never "unpark" one by guessing just to keep the ladder fed — they wait for the human (see Autonomy rules: parking / end-of-rope).

## Usage guardrail (loop semantics)

The **usage daemon** (Phase 0 step 8) keeps `~/.claude/overnight-usage.json` fresh every ~3 min. The snapshot's `source` field tells you how far to trust the number — and `check_usage.sh` is **source-aware**: a daemon-written estimate is answered with `EST_*` tokens (never plain `OK`/`PAUSE`), so a guess can never masquerade as a real reading.
- `real` — the true server % pushed by the status line. **Authoritative, but only when FRESH and IN-BLOCK:** trust it just if `age < ~210s` AND `resets_at > now`. A stale or past-reset `real` reading is the exhausted OLD block (the "RECHECK 97" trap) — do NOT trust it; use the forcing function below.
- `real-api` — the true percentages fetched by the daemon from Anthropic's own usage endpoint (`GET /api/oauth/usage`, the same data as the app's usage popup): the 5-hour window **and every weekly window including per-model ones** (the guardrail `seven_day` is set to the WORST enforceable weekly). Auth comes from the **terminal CLI's Keychain credentials** (`Claude Code-credentials`; one-time "Always Allow" for `/usr/bin/security`, granted during setup — see launch-guide). Fully authoritative — `check_usage.sh` answers it with plain `OK`/`PAUSE`/`WEEKLY_CAP` — and while it's delivering, the daemon self-tightens to a **~60s refresh**. If the CLI isn't installed/logged in, or its token has expired (the CLI refreshes it whenever it runs; the daemon deliberately never refreshes tokens itself), the daemon says why on stderr and falls through to the estimate. Kill switch: `OVERNIGHT_REAL_API=off`.
- `real-app` — the true 5h/7d %, harvested from the Claude **Desktop** app's cache. Authoritative when present, but the app only samples while it's foregrounded, so it's often stale — corroboration, not a dependable live source.
- `estimate-ccusage` — a proxy from ccusage's block-aligned, deduped active block, converted to % by a ccusage-basis calibration learned from real readings. The basis is the block's model-weighted **costUSD** (`OVERNIGHT_CCUSAGE_100PCT_COST`, default 410) — ccusage prices Opus ~5x Fable, so cost tracks the 5h limit correctly in **both** regimes: high on Opus-heavy work, low on cheap Fable work. **Output tokens** (`OVERNIGHT_CCUSAGE_100PCT`, default 2.11M) are only a *fallback* for the rare case where ccusage reports no cost — a raw token count is model-BLIND, so it both under-reads Opus-heavy windows (the 44%-vs-real-79% bug, 2026-07-21) and over-reads cheap ones (false pauses). A proxy either way, and the tools enforce the caution for you: **estimates pause at the `OVERNIGHT_EST_THRESH` margin (default: 10 points below the main threshold — 85% at the default 95), never at the full threshold.** `cc_calibrated:false` = still on a seed constant, even lower confidence.
- `unknown` — ccusage was unavailable and no real reading exists. `check_usage` reads this as `NO_DATA`; never read it as 0% or 100%.

**The forcing function (the real fix for MISSING / STALE / post-reset).** This loop IS the process making the API calls — every turn it takes, Claude Code receives the real rate-limit headers and rewrites the `source:"real"` snapshot. So a bad reading self-heals in one turn: on `MISSING`, `STALE`, `NO_DATA`, `RECHECK`, or any `real`/`real-app` reading whose `resets_at` has already passed, **do NOT pause on it** — take ONE cheap turn (you're turn-based anyway, so it's nearly free: a small real work step or even re-reading the plan), then re-run the usage check. The snapshot is now fresh `source:"real"`. Only pause when a FRESH, in-block reading (real, or an `EST_PAUSE`) is genuinely at/over its threshold. This is why the loop never needs an external poller for ground truth.

> **Caveat — where `real` readings actually come from.** The status-line forcing function only works in a **terminal `claude` session**. In the **Claude Desktop app** the CLI runs headless (`--output-format stream-json`), the status line never fires, and the app stopped writing `plan-usage-history.json` (~July 2026) — so desktop runs get no `real`/`real-app` snapshots. **That's what the `real-api` source is for**: with the terminal CLI installed + logged in (see launch-guide, incl. the one-time Keychain "Always Allow"), the daemon fetches the true percentages every ~60s no matter where the loop runs. The full ladder is: statusline `real` (terminal runs) → `real-api` (Keychain-authenticated) → `real-app` (dead on current app versions) → cost-weighted `estimate-ccusage`. Token hygiene rules for `real-api`: read the token only from the CLI's own credential store (env or Keychain), never scrape it from another process's environment, never refresh/rotate tokens (that can log the CLI out — an expired token just means "run `claude` once"), and never write the token to disk, argv, or logs.

Check with the fully-resolved command from the plan header (thresholds are two separate arguments):

```
bash ~/.claude/overnight-loop/check_usage.sh 95          # or: check_usage.sh 95 90  (two arguments) for a tighter weekly cap
```

Act on the first token: `OK` → continue; `PAUSE` → pause (below); `RECHECK` → one cheap turn then re-check; `WEEKLY_CAP <7d%> <secs> <hh:mm>` → pause `<secs>` seconds, then re-check and resume (this is the one pause that can last days — the platform freezes the session anyway; you do **not** stop); `EST_OK`/`EST_PAUSE` → the estimate's margin is already applied — treat `EST_PAUSE` exactly like `PAUSE`; `STALE`/`MISSING`/`NO_DATA` → flying blind, prefer smaller units, check more often, pause earlier. **Before any single large operation, check usage even mid-item.**

## Pause handling

`check_usage.sh` gives the exact seconds to reset (`PAUSE`, `EST_PAUSE`, and `WEEKLY_CAP` all carry a `<secs>` field). The one-time machine setup (`install.sh`) sets `"env": {"BASH_MAX_TIMEOUT_MS": "18300000"}` in `~/.claude/settings.json` so a single long sleep is *allowed* — **but the default per-call timeout is still 2 minutes, so you MUST request the long timeout explicitly on the sleep call itself**: run the pause as ONE Bash call with `sleep $((secs + 120))` and an explicit timeout parameter of `(secs + 120) * 1000` ms (capped at 18300000). Before sleeping: commit any WIP (`loop: WIP — pausing at <pct>% until <hh:mm>`) and log the pause in the report's `Pauses` section; after waking, re-check once. Fallback if the env var is missing (preflight warns about this): repeated `sleep 540` calls, each with an explicit 600000 ms timeout, re-checking after each, no other output while waiting. Do not try to set the env var mid-run — settings env is read at session startup, so it cannot take effect for the current session. **A pause is not a stop — always resume the loop afterward.**

## Autonomy rules

- Ambiguity → choose what a sensible engineer would, log it ("Assumed X because Y"), continue.
- Unspecified sub-decisions (library, naming, minor UX) → decide and log. Anything drastic the user didn't ask for → do the non-drastic version, recommend the rest in the report.
- **Parking rule — direction-ambiguous work is parked, not guessed and not asked about.** If an item needs direction only the user can give — two genuinely different products could result, or a wrong guess would mean hours of building in the wrong direction — do NOT build a guess and do NOT ask. Move it to `## Parked — needs direction` in the plan with the exact question and the options you see, mirror the question into the report's `## Questions for you`, and take the next unambiguous item. Parking is for real forks in direction; routine engineering judgment is still yours.
- **End-of-rope rule — the ONLY questions allowed after the Phase 0 window.** Only when ALL of these hold: the official mission is exhausted, a full ladder cycle produced nothing that clears the value bar, and everything left is parked-for-direction — i.e. you are seriously stuck with NO buildable work at all — may you ask. Then ask everything as ONE batch (in `## Questions for you`, and print it as a heartbeat card so it's the first thing the user sees), set the status file to `blocked|awaiting direction|`, and wait in long sleeps, re-checking for an answer after each. Asking while real work remains is a violation; so is manufacturing churn to avoid reaching this point (the anti-churn bar still applies).
- Communication during the run is minimal — progress lives in the plan, commits, and report, not in narration.

## Safety rails (non-negotiable while unattended)

- Branch only; never force-push, never rewrite history, never touch main. Push after every commit if a remote exists (the pushed branch is the backup).
- Encode the hard limits as `permissions.deny` rules in **the PROJECT's `.claude/settings.local.json`** (never hand-edit the global `~/.claude/settings.json` mid-run), using the colon prefix syntax: `Bash(git push --force:*)`, `Bash(git push -f:*)`, `Bash(git clean:*)`. Safe recipe — write the whole file if it doesn't exist, otherwise merge with python (read → modify → json-validate → replace, mirroring install.sh's pattern):
  ```
  mkdir -p .claude && { [ -s .claude/settings.local.json ] || echo '{}' > .claude/settings.local.json; }
  python3 -c 'import json,os; p=".claude/settings.local.json"; cfg=json.load(open(p)); d=cfg.setdefault("permissions",{}).setdefault("deny",[]); [d.append(r) for r in ["Bash(git push --force:*)","Bash(git push -f:*)","Bash(git clean:*)"] if r not in d]; tmp=p+".tmp"; json.dump(cfg,open(tmp,"w"),indent=2); json.load(open(tmp)); os.replace(tmp,p)'
  ```
  (One-line python on purpose — a heredoc inside this indented list would not survive a verbatim copy.)
  Wrap-up removes these rules again (see On stop) so they never outlive the loop.
- No deploys, no publishing, no package releases, no production-DB migrations, no paid signups.
- No destructive filesystem/git operations beyond the working tree.
- Installing dependencies needed for the work is fine; adding heavyweight new infrastructure is a recommendation, not an action.
- No secrets: never print, commit, or move credentials/keys (Phase 0 step 3's snapshot exclusion is the floor, not the ceiling).

## Context resilience

Over a multi-day loop the context will compact many times. The `SessionStart` loop hook (installed by `install.sh`, matcher `startup|resume|compact`, gated on the loop flag) makes recovery automatic — a resumed/compacted/fresh session in the loop's project is handed the plan, report status + tail, and git log and told to continue (never stop). On every recovery, FIRST verify the machinery is still armed — this is exempt from any "don't redo Phase 0" intuition:
1. **Daemon alive?** `ps -p "$(cat ~/.claude/overnight-usage-daemon.pid 2>/dev/null)" -o command= 2>/dev/null | grep -q usage_daemon` — a bare `kill -0` is not enough (a recycled PID after a reboot passes it). If the check fails, relaunch Phase 0 step 8's `nohup` command (the daemon's heartbeat is also what keeps the watchdog armed).
2. **Flag present with this project's cwd?** If not (crash cleanup, heartbeat expiry, or another loop overwrote it), re-arm it exactly as in Phase 0 step 7.
Then the manual fallback on any confusion: re-read `OVERNIGHT_LOOP_PLAN.md`, then `OVERNIGHT_LOOP_REPORT.md`, then `git log --oneline -20`, then continue from the top open item or the current ladder rung. The Stop-hook watchdog will block a premature end while the flag is present and fresh — if you find yourself resumed after "stopping," just continue.

## Rolling report (there is no final wrap-up — the loop maintains it live)

Keep `OVERNIGHT_LOOP_REPORT.md` (from `references/templates.md`) current after every item and every ladder rung, so a crash always leaves a useful state:
- A top **STATUS line** kept current: `LOOPING · <cycles> ladder cycles · <done> items done · build+tests <GREEN|RED> · last: <what> · monitor: <mode>`.
- **Cycle log** — one compact line per completed ladder cycle (what QA/audit/QoL produced). Summarize old cycles into a running tally so the file doesn't grow unbounded; keep the last few cycles in detail.
- Completed / Skipped / Blocked(env) / Decisions / Test status (baseline vs latest) / Pauses / Commits, plus the `Baseline` block and a machine-readable JSON footer.
- **On stop** (flag removed or user says stop, or a set ceiling passes — the watchdog blocks once with "run Wrap-up now" when a ceiling fires, then releases): finalize the report with a TL;DR verdict line, push the branch (or `git bundle` if no remote), remove the loop's deny rules from `.claude/settings.local.json`, optionally fire the local banner — exactly this command, nothing fancier:
  ```
  osascript -e 'display notification "Overnight loop finished — see OVERNIGHT_LOOP_REPORT.md" with title "overnight-loop"'
  ```
  — and remove the flag if it's still present (`rm -f ~/.claude/overnight-loop-active ~/.claude/overnight-loop-active.wrapup`). Only then end.

The flag lives OUTSIDE the project (`~/.claude/overnight-loop-active`); the plan and report live IN the project and are committed with the work. The loop changes real code by design — that is the difference from `/audit`, which changes nothing.
