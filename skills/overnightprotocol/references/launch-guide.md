# Launch guide — read this before the first loop run

The skill controls Claude's behaviour *inside* the session; the loop itself runs on Claude
Code's built-in `/loop`. **There is nothing to install.** A few things *outside* the
session still decide whether a multi-day loop survives, and only you can set them up.
**This loop does not stop on its own — read "Stopping the loop" first.**

## Upgrading

**From v0.2.x (hooks + usage daemon):** end any old loop that's still running (type `END OVERNIGHT
LOOP` in its chat), then preview and run the one-time cleanup:

```bash
bash ~/.claude/skills/overnightprotocol/scripts/uninstall-legacy.sh --dry-run
```

```bash
bash ~/.claude/skills/overnightprotocol/scripts/uninstall-legacy.sh
```

What the cleanup does:
- It refuses a real run while an old loop is still live (the dry run always works).
- It backs up `settings.json` and removes only the overnight hook entries. Your other hooks, even oddly-shaped ones, stay exactly as they were.
- It keeps your everyday `model | 5h wk ctx` status line.
- It leaves a symlinked `settings.json` a symlink, and keeps the file's permissions and characters.
- It moves the old runtime and state files to the Trash; nothing is deleted.

Then **quit and reopen Claude Code** — open sessions keep the old hooks loaded until restarted.

**From any older version, when installing from a zip:** move the old
`~/.claude/skills/overnightprotocol` folder to the Trash first. Unzipping over it leaves
retired files behind (v0.2.2's `install.sh` would even re-add the old hooks).

## One-time setup

**1. Know how usage limits resume — it depends on where you run the loop.**

- **Claude Desktop app** (the usual route). The app's own **"Auto-continue when limits
  reset"** is on by default. It resumes **5-hour limits only**, about 90 s after the reset,
  if:
  - the loop's chat is **open on screen** (its window or a split pane);
  - its message box is **empty** — no half-typed draft;
  - the Mac didn't sleep more than 6 h past the reset.

  It restarts the chat by posting "I hit my usage limit while you were working…" for you.
  **Weekly and model-specific limits never auto-resume** — the loop waits until you're back
  (everything is committed, so nothing is lost). If you run several loops at once, only
  the chats visible on screen resume.
- **Terminal CLI.** The `autoContinueAtUsageLimit` setting (on unless turned off). Three
  traps, all checked by preflight:
  - it's **off** if any project settings file mentions the key (even set to `true`),
    unless `~/.claude/settings.json` itself sets it to `true`;
  - it's **off** if a settings file doesn't parse;
  - it's **off** if Remote Control starts with every session (`remoteControlAtStartup`).

  It also won't wait for a reset more than 24 h away, goes stale ("press enter") if the
  machine slept across the reset, and gives up after repeated hits.

**2. Permissions (the real "no questions" switch).** One unanswered prompt stalls the loop.
- **Desktop app:** bypass permissions is the most reliable way to keep a loop session going. In Auto mode,
  Claude Code 2.1.274 sends every wake-up the loop schedules to the safety classifier for
  review. If a review ever said no, a Desktop loop would end, since nothing retries it.
  That hasn't been observed; it's a precaution.
- **Terminal:** `claude --dangerously-skip-permissions`. It still honours explicit `ask`
  rules; preflight lists any, so remove them first.
- Consider `"askUserQuestionTimeout": "5m"` in `~/.claude/settings.json`, so a stray
  question dialog auto-continues instead of waiting all night (confirmed for the terminal
  CLI; the Desktop app may show questions its own way).
- Either way the loop writes its hard limits as **deny rules** in the project's
  `.claude/settings.local.json` (`scripts/deny-rules.sh`). Claude Code enforces deny rules
  even with prompts bypassed. For plain `git push`, `git -C … push` and `git -c … push` they block:
  - force pushes (`--force`, `--force-with-lease`, `-f`, `+branch`), `--mirror`, `--all`, `--prune`;
  - remote branch deletion;
  - pushes to main, master or the base branch.

  They also block `gh pr merge`, `git reset --hard`, `git branch -D` and `git clean`. They
  cover the common forms — not every conceivable spelling — so the instructions still
  matter. Neither the rules nor their record is ever committed. The loop removes exactly
  the rules it added when it stops. When it upgrades a run started by v0.3.0, it also takes
  over and removes the three colon-form rules that version wrote. If a run died without
  wrapping up, remove them with:

  ```bash
  bash ~/.claude/skills/overnightprotocol/scripts/deny-rules.sh remove /path/to/project
  ```

  The rules use Claude Code's wildcard form (`Bash(git push *--force*)`); the older
  `Bash(cmd:*)` prefix form also still works.

**3. Full Disk Access** (System Settings → Privacy & Security → Full Disk Access) for your
terminal app, if you use one, granted **before** the first run. A mid-run restart can
otherwise revoke access to your project, and every shell command fails with "Operation not
permitted". (The loop backs off hourly if that happens.)

**4. Keep the Mac awake — and keep Claude Code open.**
- A sleeping Mac or a quit app can't resume after a limit. The Claude Desktop app usually
  holds its own "no idle sleep" assertion while it works, and preflight shows who is
  keeping the Mac awake. It counts only real keep-awake owners: caffeinate, the Claude
  app, Amphetamine, KeepingYouAwake, Lungo, Theine, Caffeine. A screen that's merely on, or
  a brief Handoff or video, doesn't count.
- If nothing is, run this once in any terminal:

  ```bash
  caffeinate -dis &
  ```

  It keeps the Mac awake **only on AC power**. It stops if that terminal window closes;
  prefix it with `nohup` to survive that.
- **Terminal route:** launch inside `caffeinate -dis tmux new -s loop` instead.
- Check what's holding the Mac awake:

  ```bash
  pmset -g assertions | grep -E 'pid [0-9]+\('
  ```

  A line from `powerd` only means "someone is at the Mac" and doesn't count.
- **Keep the lid open** unless you have an external display and keyboard (clamshell mode).

## Launching a loop

Open the project (Desktop app Code tab, or `claude --dangerously-skip-permissions` from the
project folder) and send a kickoff like:

> Overnight loop. Tasks: [the list, or "none — just keep improving it"]. Keep building
> and improving non-stop until I stop you. I'm AFK.

The skill:
1. runs the preflight doctor;
2. sends **one kickoff message** with:
   - the Stop button;
   - which uncommitted files it would commit or skip;
   - its questions, each with the default it will use. The first is the branch: stay on your current branch, or switch to a new `overnight-loop/<date-time>` one. On a feature branch the default is to stay; on `main` it always uses a new branch. Name the branch in your kickoff message and it won't ask;
3. waits up to about 4 minutes for your answers — reply straight away and it carries on at once;
4. branches, snapshots, writes the plan and report plus a `.overnight-loop/` folder, captures a baseline, adds the deny rules, and starts `/loop`.

After that, every turn schedules the next. When your list is done, it moves on to the ladder (QA → /audit + fix → research + QoL), forever.

## Watching it run

- **In the app:** the Stop button, plus a heartbeat card after each finished item or rung change (cycle · items done · build status · current item).
- **On disk:** `OVERNIGHT_LOOP_REPORT.md` in the project. Its STATUS line is always current, and "Questions for you" and "Recommendations" collect what it wants you to decide. Every loop commit:

  ```bash
  git log --oneline --grep='^loop'
  ```

- **Chatting with it mid-run is fine.** It answers, then carries on. Any question it asks you comes with a default and never blocks its work.

## Stopping the loop (important — it will not stop on its own)

Press the **Stop button**, or type **`END OVERNIGHT LOOP`**. Claude then:
1. stops its background tasks;
2. finishes or shelves the current item;
3. commits the final report and pushes;
4. removes its deny rules;
5. notifies you, and ends `/loop`.

A hard ceiling set at kickoff ("loop until 6am") does the same at that time.

**Interrupting a turn (Esc, or the app's stop control) pauses the loop — it does not end
it cleanly.** Nothing is scheduled mid-turn, so no next tick fires. Send any message
afterwards (e.g. "carry on") and it re-arms; or type END OVERNIGHT LOOP to end it properly.

## If the run died (resume)

Nothing is lost — the plan, report, `.overnight-loop/` and commits persist. Reopen the
project's session (terminal: `claude --continue --dangerously-skip-permissions`) and say:

> Resume the overnight loop.

It finds the loop branch and switches to it. If the last turn was under 2 hours ago it
first checks with you that the old session is closed. Then it recovers any uncommitted
work, relaunches interrupted background tasks, and restarts `/loop`.

## Known limits — set expectations

- **Keeping the loop alive is the agent's job.** Every turn ends by scheduling the next,
  and there's no hook forcing it. In the Desktop app a missed wake-up simply ends the loop
  (the terminal retries once after 20 min). Everything is committed; "resume the overnight
  loop" restarts it.
- **Weekly limits pause the loop until you return** — in the Desktop app always, in the
  terminal when the reset is more than 24 h away.
- **Claude Code ends a self-paced loop after about 7 days of continuous ticking.** The loop
  then pauses with a note to resume.
- **A never-stopping builder is only as safe as its reversibility.** Everything is on a
  branch, committed per item and pushed, and new secret-shaped files are never committed.
  Review the branch before merging.
