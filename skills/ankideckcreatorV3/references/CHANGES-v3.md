# What changed in v3

**One functional change: the skill no longer touches Anki's deck options.**
Everything else — content pipeline, card design, audio, delivery, linting — is
v2 unchanged (see `CHANGES-v2.md`).

## The problem

v2's `ensure_preset()` gave every deck its own options preset, cloned from
whatever preset the deck was on, then wrote `20 new/day` and `300 rev/day` into
it. Two things went wrong in practice:

1. **Orphaned presets piled up.** `cloneDeckConfigId` happily creates a preset
   whose name already exists, so any re-run that found the deck not already on
   its preset cloned *another* one. The options dropdown filled with entries
   like `JLPT Grammar House (used by 0 decks)` — three identical copies in one
   observed collection — plus `Hindi Core 2k` twice and a stray
   `Default1782687365`.
2. **It imposed scheduling nobody asked for.** The deck's pace was decided by
   the skill (20/300), not by the person studying it.

## The fix

`ensure_preset()` is replaced by `report_deck_options()` — a **read-only**
reporter. v3 makes **zero** deck-config writes:

| v2 | v3 |
|---|---|
| `cloneDeckConfigId` → new preset per deck | *(never called)* |
| `setDeckConfigId` → move deck onto it | *(never called)* |
| `saveDeckConfig` → write `perDay` limits | *(never called)* |
| `getDeckConfig` | `getDeckConfig` only, to print what the deck is on |

A brand-new deck is already on Anki's **Default** preset with Default's rules,
so "stick with the default settings" is achieved by calling nothing.

## Why it does not simply force every deck onto Default

Two deliberate refusals, both about not damaging existing study data:

* **Default is shared.** Writing `perDay` onto it would silently re-pace every
  other deck using it. (This is what v2's `DAT-default-preset-mutation-a012a981`
  guard was protecting against — v3 keeps that property *structurally*: there is
  no `saveDeckConfig` call left anywhere in the skill.)
* **Existing decks keep their preset.** Phase 8 (extend) re-runs the same
  scaffold against decks that already have review history. Reassigning one to
  Default would change its daily limits, learning steps, leech threshold and —
  under FSRS — its optimised parameters, which are stored *per preset*. So v3
  reads and reports, and changes nothing.

If a different pace is wanted, set it by hand in Anki (Deck ⚙ → Options), or
create a preset there and assign it. The skill will leave it alone.

## Files touched

| File | Change |
|---|---|
| `scripts/anki.py` | `ensure_preset()` → `report_deck_options()` (read-only, failure non-fatal); `PRESET` dropped from the `config` import; `scaffold()` calls the new function |
| `scripts/config.py` | `PRESET` constant removed, replaced by a comment explaining why there is none |
| `scripts/preflight.py` | the `preset is not Default` check removed (it asserted the opposite of v3's behaviour, and referenced the now-absent constant) |
| `SKILL.md` | frontmatter `name`, heading, supersedes note, Phase 6 step 1 |
| `references/pipeline.md` | final-audit item now verifies deck options were left untouched |

`_BOOL_RESULT` in `anki.py` is left as-is: it is inert data, and keeps the guard
in place should anyone ever re-introduce a deck-config write.
