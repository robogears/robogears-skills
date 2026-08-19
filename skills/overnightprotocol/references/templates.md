# File templates — copy these verbatim in Phase 0

Both files live **in the project** and are the loop's crash/compaction recovery state
AND the user's progress record. Fill the `<...>` placeholders; keep the headings and
their order so post-compaction appends always land in the same place.

---

## OVERNIGHT_LOOP_PLAN.md

There is no `END_EPOCH` — the loop is unbounded. `CEILING_EPOCH` appears only if the
user asked for a hard stop time.

```markdown
# Overnight Loop Plan — <YYYY-MM-DD>

<!-- run metadata — do not delete; recovery reads this first -->
START_EPOCH: <int>
CEILING_EPOCH: <int|none>   # optional hard stop; "none" = unbounded (default)
BRANCH: overnight-loop/<YYYY-MM-DD>
THRESHOLD: 95               # weekly threshold too if different — TWO separate arguments, e.g.: 95 90
USAGE_CHECK: bash "/Users/<you>/.claude/overnight-loop/check_usage.sh" 95
BASELINE: see OVERNIGHT_LOOP_REPORT.md "Baseline" block
MONITOR: <ok | estimate | blind>
STOP: remove ~/.claude/overnight-loop-active   # the only off-switch

## Mission (official items — do these first)
- [ ] 1. <item> — done when: <acceptance note>
<!-- item states: [ ] not started · [~] in progress · [x] done · [>] skipped · [!] blocked(env) -->
<!-- if the user gave no mission, leave this empty and go straight to the ladder -->

## Ladder backlog (self-generated when the mission is exhausted)
<!-- QA fixes, audit findings, and researched QoL improvements get appended here as checkboxes -->

## Parked — needs direction (never build these on a guess; see Autonomy rules)
<!-- - ⏸ <item> — question: <the exact fork> — options: <A | B> — would resume with: <what an answer unlocks> -->
```

When an item is picked, rewrite its checkbox in place:

```markdown
- [~] 3. <item> — STARTED <epoch> (attempt 1) — approach: <one line> — wip:<hash|none>
```

`- [x]` done · `- [>]` skipped(reason) · `- [!]` blocked(env, morning command).

---

## OVERNIGHT_LOOP_REPORT.md

The STATUS line under the title is kept current after every item and every ladder rung,
so even a crashed loop has it. The "On stop" section sits near the TOP on purpose, and
the SessionStart hook additionally injects the report only from "## Cycle log" onward —
belt and braces so the runnable merge-to-main commands never land in the unattended
agent's context (they are for YOU, in the morning — the loop itself must never run them).

*(The outer fence below uses four backticks so the inner code blocks nest correctly —
copy everything between the four-backtick lines.)*

````markdown
# Overnight Loop Report — <YYYY-MM-DD>

STATUS: LOOPING · <cycles> ladder cycles · <done> items done · build+tests <GREEN|RED> · last: <what just happened> · monitor: <ok|estimate|blind>

## Run
- Started: <HH:MM>  ·  Branch: overnight-loop/<YYYY-MM-DD>  ·  Ceiling: <none|HH:MM>
- Usage monitor: <real snapshot | ccusage estimate | flew blind>

### On stop — morning commands for the HUMAN (the loop never runs these)
```sh
git checkout main && git merge --no-ff overnight-loop/<YYYY-MM-DD>
# or review first, with a remote:  gh pr create --base main --head overnight-loop/<YYYY-MM-DD>
# to discard:  git branch -D overnight-loop/<YYYY-MM-DD>
```

## Cycle log
<!-- one compact line per completed ladder cycle; summarize old cycles into a tally -->
- Cycle <n>: qa <x fixed> · audit <y safe-fixes, z queued> · qol <w shipped>

## Completed
- ✅ <item> — <duration> — <commit hash>

## Blocked (environment)
- ⚠️ <item> — blocked by <reason> — run in the morning: `<exact command>`

## Skipped
- ⏭ <item> — <reason> — partial work: <patch path | none>

## Questions for you (parked forks — answer any of these to unpark the matching plan item)
- ❓ <question> — options considered: <A | B> — currently parked: <item>

## Decisions & assumptions
- <"Assumed X because Y">  ·  <"Built QoL Z because <value bar>">
<!-- includes the Phase 0 question-window assumptions that went unanswered -->

## Test status
- Baseline: <summary>   Latest: <summary>

## Pauses
- PAUSE <HH:MM> → <HH:MM> (<dur>) — 5h at <pct>%, reset <HH:MM>

## Commits
- <hash> <message>

### Baseline
```
<verbatim baseline test/build/typecheck output captured in Phase 0>
```

<!-- machine-readable footer -->
```json
{"date": "<YYYY-MM-DD>", "branch": "overnight-loop/<YYYY-MM-DD>", "cycles": <n>,
 "done": <n>, "skipped": <n>, "blocked": <n>, "state": "<looping|stopped>",
 "build": "<green|red>", "usage_monitor": "<ok|estimate|blind>"}
```
````
