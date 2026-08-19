# Worked example: greenfield app-build prompt

This is a complete, calibrated example of the output this skill produces. Note the level of concreteness: every requirement is checkable, the stack is locked, non-goals are explicit, and the acceptance criteria are commands and observable behaviors — not adjectives.

**Scenario**: User said, "write me a prompt for Claude Code to build a simple workout logger web app, nothing fancy, just for me." Two clarifying answers were obtained: local-only is fine (no accounts), and they want it fast to spin up.

---

## The delivered prompt

```
# Task: Build "RepLog" — a local-first workout logger web app

## Context
Greenfield project in an empty directory. Single user, runs locally in the
browser, no backend and no accounts. Priority is a working MVP, not
extensibility.

## Objective
A web app where the user logs workout sessions (exercises, sets, reps,
weight) and can review past sessions.

## Requirements
1. Stack: Vite + React 18 + TypeScript. No other frameworks.
2. Persistence: localStorage under the single key "replog.sessions.v1",
   storing an array of Session objects. Define the Session and
   ExerciseEntry types in src/types.ts before building UI.
3. Session create flow: date (defaults to today), then one or more
   exercise entries — each with exercise name (free text), sets (int),
   reps (int), weight in kg (number, one decimal allowed).
4. History view: sessions listed newest-first, each expandable to show
   its entries. Each session has a delete button with a confirm step.
5. Edit flow: an existing session can be reopened and its entries
   modified, then saved back.
6. Empty state: when no sessions exist, show a short message and a
   "Log first session" button.
7. Basic input validation: sets/reps must be positive integers, weight
   must be >= 0. Invalid input blocks save and shows an inline message.

## Constraints & non-goals
- Do NOT add: routing libraries, state management libraries, CSS
  frameworks, auth, backend, export features, charts.
- Plain CSS or CSS modules only. Keep it readable on a phone-width
  viewport (~380px) and desktop.
- No external network calls of any kind.

## Process
- Before coding, write a short plan: file structure and the data model.
  Then implement in increments, running the dev server after each.
- Run `npm run build` before declaring done; fix all TypeScript errors.

## Acceptance criteria (demonstrate each)
- [ ] `npm install && npm run dev` serves the app with no console errors
- [ ] Create a session with 2 exercises; it appears in history
- [ ] Reload the page; the session is still there
- [ ] Edit that session's reps; the change persists after reload
- [ ] Delete the session via the confirm flow; history shows empty state
- [ ] `npm run build` completes with zero TS errors

## Output
Final file tree, run instructions, and a 5-line summary of decisions
made (especially anything you chose that wasn't specified).

If any requirement is impossible as written or two requirements
conflict, stop and ask before working around it.
```

---

## Why it's built this way (the design notes that accompanied it)

- **Stack locked, versioned, minimal** — "Vite + React 18 + TypeScript, no other frameworks" prevents the agent importing Redux/Tailwind/React Router for a one-screen app.
- **Persistence pinned to a named key and a types-first step** — forces the data model to exist before UI, which is where solo-app builds usually go sideways.
- **Non-goals do half the work** — without them, coding agents reliably add routing, charts, and export "for completeness."
- **Every acceptance criterion is an action + observable result** — reload-persistence is tested explicitly because localStorage bugs are the most common failure for this app class.
- **Escape hatch scoped to contradictions only** — not "ask if unsure," which invites stalling.
