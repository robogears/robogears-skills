# File templates — copy these in Phase 0

Both files live **in the project** and are committed with the work: they are the loop's
recovery state AND the user's progress record. Everything else the plan depends on goes in
`.overnight-loop/` (committed: specs, workflow scripts, the baseline output, archives) or,
if it must not be pushed, in `<git-dir>/overnight-loop/` (private: audit reports, the
deny-rule record) — never only in a session scratchpad or `/tmp`. Write the home folder as
`~` in both files. Fill the `<...>` placeholders and keep the headings and their order.
When a run written by an older version is picked up, migrate it to these headings and
header fields first.

---

## OVERNIGHT_LOOP_PLAN.md

```markdown
# Overnight Loop Plan — <YYYY-MM-DD>

<!-- run metadata — recovery reads this first; update it at the end of every turn -->
STATE: LOOPING              # LOOPING | PAUSED (<why>) | STOPPED
PROJECT_PATH: <~/path/to/project>
SKILL_PATH: <~/.claude/skills/overnightprotocol/SKILL.md — wherever it is installed>
START_EPOCH: <int>
LAST_TURN_EPOCH: <int>
CEILING_EPOCH: none         # or <int> if the user set a stop time
BASE_BRANCH: <branch the run started from — never touched>
BRANCH: overnight-loop/<YYYY-MM-DD-HHMM>   # or the existing branch the user chose to work on
RUNG: mission               # mission | 1-qa | 2-audit | 3-qol
CYCLE: 0                    # completed ladder cycles
LAST_AUDIT: none            # <epoch> <commit> — re-audit only after ≥5 new loop: commits
DENY_RULES: added by scripts/deny-rules.sh — record in <git-dir>/overnight-loop/ (private)
TICK_PROMPT: <the exact tick prompt>
EVERY TURN: re-read this header + open items → handle user messages → check the ceiling → work → update this header → ScheduleWakeup(TICK_PROMPT)
STOP: the user types END OVERNIGHT LOOP (or presses the Stop button)

## Mission (official items — do these first)
- [ ] 1. <item> — done when: <acceptance note>
<!-- open = [ ] or [~]; resume [~] first
     [~] N. <item> — STARTED <epoch> · attempt <n> · approach: <one line> · active: <m>m · wip: <hash|none> [· delegated: <task/run id> · <worktree/branch>] [· interrupted]
     [x] N. <item> — <active>m
     [>] N. <item> — <reason> · shelved: overnight-loop/shelved/<slug>
     [!] N. <item> — blocked by <reason> (morning command in the report) -->

## Ladder backlog (self-generated when the mission is exhausted)
<!-- QA fixes, audit findings (ID + one-line title only — the full report stays private) and researched QoL items, as checkboxes with acceptance notes.
     Keep the last 20 done items here; move older ones to .overnight-loop/archive.md -->

## Parked — needs direction (never build these on a guess)
<!-- - ⏸ <item> — question: <the exact fork> — options: <A | B> — unlocks: <what an answer enables> -->
```

---

## OVERNIGHT_LOOP_REPORT.md

The STATUS line under the title is kept current after every item and rung, so even a
crashed loop has it. The morning merge commands are for the **human** — the loop never
runs them (it never touches the base branch).

*(The outer fence uses four backticks so the inner blocks nest — copy everything between
the four-backtick lines.)*

````markdown
# Overnight Loop Report — <YYYY-MM-DD>

STATUS: LOOPING · cycle <n> · <done> done · build+tests <GREEN|RED> · last: <what just happened>

## Run
- Started: <HH:MM> · Branch: <BRANCH> from <BASE_BRANCH> · Ceiling: <none|HH:MM>
- Commits: `git log --oneline --grep='^loop' <BASE_BRANCH>..<BRANCH>`

## Questions for you (parked forks and end-of-rope questions — answer any to unpark)
- ❓ <question> — options: <A | B> — parked item: <item>

## Recommendations (things the loop chose not to do unattended)
- 💡 <drastic or needs-verification change> — why: <reason> — how: <one line>

## Progress log (last 20; older lines roll into the tally)
- <HH:MM> <what happened>
<!-- tally: <n> older entries archived to .overnight-loop/archive.md -->

## Cycle log
- Cycle <n>: qa <x fixed> · audit <y fixed, z queued> · qol <w shipped>

## Completed
- Total: <n> · recent: ✅ <item> — <active>m

## Blocked (environment)
- ⚠️ <item> — blocked by <reason> — run in the morning: `<exact command>`

## Skipped / shelved
- ⏭ <item> — <reason> — branch: overnight-loop/shelved/<slug>

## Excluded from commits (secret-shaped or too big — commit them yourself if they're fine)
- <path> — <reason>

## Decisions & assumptions (last 20 + tally)
- <"Assumed X because Y"> · <"Built QoL Z because <value bar>">
<!-- includes the kickoff question-window defaults that went unanswered -->

## Test status
- Baseline: <≤10-line summary; full output in .overnight-loop/baseline.txt>
- Latest: <summary>

## Usage-limit waits
- LIMIT <HH:MM> → resumed <HH:MM> (<how: Desktop auto-resume | terminal auto-continue | you>) — WIP <hash|none>

## Morning merge — for YOU (the loop never runs these)
```sh
git log --oneline <BASE_BRANCH>..<BRANCH>          # review
git checkout <BASE_BRANCH> && git merge --no-ff <BRANCH>
# with a remote, review as a PR instead:  gh pr create --base <BASE_BRANCH> --head <BRANCH>
# if the branch tip is an unverified WIP commit (its message ends "(unverified)"), merge the newest verified one instead:
#   git merge --no-ff "$(git log -1 --format=%h --invert-grep --grep='(unverified)' <BRANCH>)"
# to discard a loop-created branch:  git branch -D <BRANCH>
```

<!-- machine-readable footer -->
```json
{"date": "<YYYY-MM-DD>", "branch": "<BRANCH>", "base": "<BASE_BRANCH>",
 "cycles": <n>, "done": <n>, "shelved": <n>, "blocked": <n>,
 "state": "<looping|paused|stopped>", "build": "<green|red>"}
```
````
