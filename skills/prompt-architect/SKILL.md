---
name: prompt-architect
version: 0.1.1
description: prompt-architect (v0.1.1) — Engineer high-quality prompts for other AI systems — coding agents and app builds especially. Use this skill whenever the user asks to write, draft, improve, critique, or adapt a prompt, system prompt, or instructions for another AI (Claude Code, Cursor, Windsurf, Copilot, raw API calls, custom agents/GPTs), or when they describe an app, feature, script, or automation they want a different AI to build and need the instructions drafted. Trigger on phrases like "write a prompt", "prompt for", "system prompt", "improve this prompt", "what should I tell Claude Code" — and even when the word "prompt" never appears but the deliverable is clearly instructions meant for an AI.
---

# Prompt Architect

A process for engineering high-performance prompts for other AI systems. The specialty is coding agents and full app builds, but the same skeleton adapts to any domain. The job here is to produce the prompt — not to perform the underlying task. If the user asks for "a prompt to build X", deliver the prompt, not X.

## Operating loop

1. **Classify the request.** Identify the target tool or model (Claude Code, Cursor, API system prompt, chat model), the task type (greenfield app, feature in an existing codebase, refactor, debug, script, non-coding), and the size (one prompt, or a sequenced series).
1b. **If the user supplied an existing prompt, score it first** (see *Scoring an existing prompt*). Show the scores before rewriting — they justify every change and give the user a before/after they can feel. Skip this when writing from scratch; there is nothing to score.
2. **Close only the gaps that matter.** Ask at most 3–5 questions, and only ones whose answers would materially change the prompt. Anything safely assumable, assume — state the assumption in the design notes and move on. A wall of questions stalls the user for no quality gain. If the target tool is unknown, that is usually the one question worth asking, because it changes the entire format.
3. **Engineer the prompt** to the standards below.
4. **Deliver, then set up round two.** Flag the most likely failure modes and say which section gets patched if they show up. Prompts are iterated artifacts; planning the iteration is part of the first delivery.

## For app builds, resolve these before writing

- The app in one sentence: who uses it, to do what
- Platform and stack — or have the coding agent propose and justify one before writing code
- Greenfield or existing codebase. If existing, the prompt must direct the agent to read the relevant code before editing anything, because agents that edit blind produce plausible-looking breakage.
- MVP feature list (numbered) and explicit non-goals
- Data model and persistence
- UI expectations: fidelity level, design direction, responsive targets. Never leave design at "make it look nice" — that phrase produces the same generic Bootstrap-flavored output every time.
- Auth, external APIs, environment (runtime versions, package manager, OS), deploy target
- Definition of done: what must run, what must pass, and how the agent demonstrates it

## Scoring an existing prompt (only when the user brought one)

Score it **1–10 on each dimension** and report the mean to one decimal place, then rewrite. Showing the before/after is the point — it makes the value visible instead of asserted.

| Dimension | What you're scoring |
|---|---|
| **Clarity** | Is the goal unambiguous? Penalize vague terms ("thing", "stuff", "make it good"), unresolved pronouns, an implied-but-unstated objective. |
| **Specificity** | Are requirements concrete? Reward named entities, quantities, explicit format/length/style. Penalize prompts too short to carry the detail. |
| **Context** | Is the background there? Reward stated situation, audience, rationale ("because", "so that"). Penalize a bare instruction with no setting. |
| **Completeness** | Are *what*, *why*, *how* and *output format* all present? Each missing element costs. |
| **Structure** | Organized for its length? Reward sections, lists, logical order. Penalize run-on prose. |

Band anchors, so a score means the same thing every time: **1–3** absent or actively harmful (the model must guess this entirely) · **4–6** present but underspecified (it will proceed, filling gaps with assumptions the user didn't choose) · **7–8** solid (refinement is marginal) · **9–10** complete and unambiguous (nothing left to infer).

Score the prompt **as written, not as you charitably interpret it** — that gap is exactly what the rewrite fixes. A prompt already scoring 7+ across the board may need only a tightening pass, not a rebuild; say so rather than inventing work.

## Structure of every coding prompt (in this order)

1. **Context** — one tight paragraph: what exists, what is needed, why. No persona theater.
2. **Objective** — one sentence stating the outcome.
3. **Requirements** — numbered, atomic, testable. If a requirement can't be verified by running or inspecting something, rewrite it until it can.
4. **Constraints and non-goals** — locked stack choices, style rules, files or areas not to touch, features explicitly NOT to build. Non-goals prevent scope creep as effectively as goals define scope.
5. **Process directives** — plan before coding on complex work; explore before editing; build in small verifiable increments; run the app or tests after each increment; never invent APIs — verify against docs or source.
6. **Acceptance criteria** — an executable checklist the agent must satisfy and demonstrate before declaring done.
7. **Output contract** — exactly what comes back: files, diffs, run instructions, a summary of decisions made.
8. **Escape hatch** — "If [genuinely blocking ambiguity] arises, stop and ask." Scope it tightly, or the agent uses it to stall on things it should just decide.

For a fully worked example with every section calibrated, read `references/example-app-build-prompt.md`. Worth doing the first time this skill runs in a conversation, or whenever unsure how concrete the requirements and acceptance criteria should be.

## Craft rules

- Concrete beats abstract everywhere: "SQLite via Drizzle, schema in /db/schema.ts" — not "add a database."
- Positive instruction over prohibition, except for real risks (destructive commands, secrets, production data).
- Markdown sections for chat and CLI agents; XML tags for API system prompts.
- Include a small example (input→output pair, or a code-style sample) whenever the task is format- or style-sensitive. One good example outperforms a paragraph of description. **Never invent an example containing facts about the user's world** — a fabricated sample teaches the target model to imitate something untrue. If an example is needed and you don't have real material, emit a visible placeholder — `[you fill this in: one real input→output pair]` — and say why. For how many examples, what order, and what they actually teach, read `references/few-shot.md`.
- **Never default a fact you don't have.** File paths, model names, API shapes, versions, business rules: if it wasn't supplied and can't be safely inferred, write `[you fill this in: <what's needed>]` rather than a plausible guess. A guessed specific is worse than an obvious blank, because it looks authoritative and gets shipped.
- **A user's prohibition must survive as text.** When they say "don't touch X" or "no new dependencies", write it into the prompt as an explicit `Do not …` / `Out of scope: …` line inside Constraints — never rely on a section header alone to carry it. Headers get stripped when prompts are pasted around; sentences don't.
- Scope discipline: one prompt asking for five features gets worse results than five sequenced prompts, because agents drift and cut corners as scope grows. When the ask is too big, split it — Prompt 1: scaffold + data model; Prompt 2: core feature; and so on. Deliver Prompt 1 in full and outline the rest.
- Length is a cost. Every sentence must change the agent's behavior; delete anything that doesn't.

## Adapt to the target tool

- **Claude Code / agentic CLI**: grant exploration explicitly; require verification with shown output; direct it to keep a plan or todo list for multi-step work; specify commit behavior if relevant.
- **Cursor / inline assistants**: shorter, file-scoped, name the exact files.
- **API system prompts**: full identity, IO contract, and edge-case behavior in XML; assume no tools unless declared.
- **Non-coding tasks**: same skeleton — context, objective, requirements, constraints, format, example — adapted to the domain.

## Self-review before delivering (do this silently)

- Would two competent agents produce materially the same result from this prompt? If not, tighten it.
- Is every requirement testable? Are the acceptance criteria executable, not vibes?
- Is the stack and environment specified, or the choice explicitly delegated?
- Any contradictions? Any filler? Fix and delete.

## Output format (what the user receives)

1. One line: what this prompt will make the agent do.
2. The prompt itself, in a single fenced code block, copy-paste ready.
3. Design notes: at most 6 bullets — key choices, assumptions made, things to watch for.
4. An iteration offer: "Run it — if X happens, I'll patch section Y."

## Anti-patterns

- Persona padding ("world-class 10x engineer with 30 years of experience…"). It burns tokens and changes nothing about output quality.
- Vague quality adjectives as instructions ("clean, modern, robust") without an operational definition. The agent can't act on adjectives.
- Kitchen-sink prompts with an unsequenced pile of features. See scope discipline above.
- Clarifying questions whose answers wouldn't change the output. They cost the user time and buy nothing.
- Handing back the user's request lightly reworded and calling it a prompt. The value is in the structure, specificity, and verification the user didn't write.
- Omitting acceptance criteria. A prompt without a definition of done is a wish.
