---
name: ankideckcreatorV3
version: 0.1.3
description: ankideckcreatorV3 (v0.1.3) — THE current Anki deck builder — supersedes ankideckcreator and ankideckcreatorV2 (both retired; this is the only one). Build a complete, studiable Anki deck in William's house style — agent-written and adversarially-verified card content, an example sentence on the back of every card, neural TTS audio on every card, the signature parchment/indigo/marigold card design, delivered live into Anki via AnkiConnect with an .apkg backup. Use this whenever the user asks to make an Anki deck, flashcards, a vocab deck, a "core" deck for a language, study cards for any topic, or says anything equivalent ("make me cards for X", "add a deck for Y", "I want to memorize Z") — even if they don't say the word Anki. Also use it to EXTEND an existing house-style deck (adding cards to a deck this skill built earlier, e.g. a sentence-mining deck feeding into the same note type).
---

# Anki Deck Creator (v3)

Reproduce the pipeline that built William's Hindi Core 2k — the deck he called perfect — for whatever topic follows the command. The two things he loves are (1) the card design and (2) the content quality that came from adversarial verification. Never economize on either.

The finished deck always has, on every card: a headword, a concise gloss, a part-of-speech / category line, an example sentence ON THE BACK with its translation/explanation, neural TTS audio for both word and sentence, and the signature design. Cards are ordered most-useful-first.

> **v3** supersedes `ankideckcreatorV2` (and `ankideckcreator`), both kept unchanged for reference. **The one change in v3: the skill no longer touches deck options.** It creates no preset, edits none, and moves no deck between them — a new deck simply keeps Anki's Default preset and its rules. See `references/CHANGES-v3.md`. Everything else is v2, whose fixes are listed in `references/CHANGES-v2.md`.
>
> The v2 inheritance: the record schema matches what the scripts actually read, the scaffold builds the approved design and its `Hint` field, delivery cannot mass-duplicate notes after a crash, failed audio never deletes good clips, the nuqta step cannot silently do nothing, and the quality checks catch *missing* pronunciation marks as well as wrong ones. All configuration lives in ONE file: `config.py`.

## Phase 0 — Scope the deck (one quick exchange, or infer)

Determine from the request: **topic**, **deck size** (default 1000 for a language core deck, 100–300 for a topic deck; honor any number the user gives), **target deck name** in Anki, and **language of the audio**. If the user's request pins these down, do not ask — start building. Ask only when a wrong guess would waste the whole build (e.g. which language variant, or romanization preferences for a non-Latin script).

For language decks in a non-Latin script, ask how they want it romanized unless a convention is already in memory. William's Hindi decks use "precise Hinglish" — read `references/hindi.md` before building ANY Hindi/Indic deck; it also covers nuqta restoration and the capitalization rule.

Check memory for an existing house-style deck on an adjacent topic first — extending an existing note type beats creating a parallel one. **To extend a deck this skill already built, go to Phase 8.**

**Set up the workspace before anything else:**

```bash
cp -R <skill>/scripts <scratchpad>/deck
mkdir -p <scratchpad>/deck/{batches,built,final}
python3 -m venv <scratchpad>/env && <scratchpad>/env/bin/pip install -r <skill>/requirements.txt
cd <scratchpad>/deck          # run every script from here
```

**Always copy `scripts/` into the session scratchpad and work there.** Every script anchors its state next to its own file, so running them inside the skill directory writes build state into the skill itself and leaves it for the next session to trip over. `batches/`, `built/`, `final/`, `manifest.json` and `audio_stamps.json` all live **inside that copied directory** — that is where the scripts look for them.

Then edit `deck/config.py` — that ONE file holds the deck name, note type, media prefix, deck size, voice, and the accent code. Nothing else needs editing.

**For a non-Hindi deck, set `ACCENT_CODE = None` in config.py.** Left on, the Hindi transforms apply to everything: "Paris" is silently lowercased to "paris" and any capital T/D/R is marked as a retroflex pronunciation cue.

## Phase 1 — Candidate list

The deck teaches the most useful N items, in order of usefulness. Where the ordering comes from depends on the deck:

- **Language vocab**: a frequency list. Use Python `wordfreq` (`top_n_list(lang, N*1.3)`) as the primary rank source; it blends corpora and has a stable tail. **Overshoot by ~30%** — roughly a quarter of candidates get rejected (proper nouns, junk tokens, raw loanwords, register too formal for a learner, inflected forms whose base form suffices). `assets/hindi-extras/prep.py` and `extend.py` show the full pattern including mining Tatoeba for real example sentences (per-language exports at `https://downloads.tatoeba.org/exports/per_language/`; join hin_sentences + links + eng_sentences).
- **Topic decks** (music theory, world capitals, anatomy…): curate the list yourself ordered by pedagogical priority, or from an authoritative source. Same overshoot principle if filtering happens later.

Write candidates as batch files of ~184 items each (`batches/batch_NN.json`), each item carrying rank, the term, and any machine drafts (romanization, candidate sentence), so builder agents fix drafts rather than create from nothing. Also persist the full candidate list once (`batches/candidates.json`) — Phase 8 needs it to extend the deck later without re-deriving a list that may have shifted.

## Phase 2 — Build + adversarially verify (Workflow tool)

This is where the quality came from. Every batch gets TWO agents: a builder, then a verifier whose job is to assume the builder made mistakes and hunt for them. On the Hindi build the verifiers caught wrong noun genders, glosses that only fit a rare sense, sentences that didn't contain their own target word, a factual error, and a romanization that taught the wrong consonant. Do not skip verification, and keep it adversarial — a "check this over" prompt finds nothing; a "the learner will rehearse any error you miss hundreds of times, assume there are errors and find them" prompt finds real ones.

Read `references/pipeline.md` for the exact workflow script pattern and both prompt templates. **If the Workflow tool is unavailable, the file contract is the invariant** — `batches/` → `built/` → `final/`, one Write per agent, small structured summaries returned — and plain parallel subagents launched in build-then-verify pairs satisfy it identically.

Key mechanics learned the hard way:

- Builders and verifiers WRITE their full output to files (`built/batch_NN.json`, `final/batch_NN.json`) and return only a small structured summary — full content through StructuredOutput blows up. Tell them explicitly to write in ONE Write call.
- Pipeline batches (`pipeline()`), don't barrier them — verification of batch 0 runs while batch 5 still builds.
- Session limits can kill agents mid-flight. The file-based design makes resuming trivial: check which `built/`/`final/` files exist, relaunch only the missing ones.
- **The record schema is defined once, in `record.py`** — read it and quote it into the agent prompts verbatim:
  `{rank, term, headword, keep, reason?, gloss, pos, sent_native, sent_display, sent_en, source_id, hint?}`.
  `term` is what TTS speaks (native script); `headword` is what the learner reads; for a deck with no separate script they are the same. Rejected items stay in the file with a reason — that's the audit trail.

## Phase 3 — Deterministic quality passes (scripts, not agents)

Agents catch semantic errors; scripts catch mechanical ones, more reliably and for free. Run these from your scratchpad `deck/` copy (`<skill>` below is the skill's own directory — the hindi-extras scripts stay there and read/write the directory you run them FROM):

1. **`python lint.py final`** — empty fields, duplicate/missing ranks, script leakage, spelling-spec violations, target-word-not-in-sentence, sentence-pair mismatches, raw HTML in content, retroflex marks the native script does not justify **and retroflex marks that are missing**, and homographs whose hints are identical. `--fix` repairs the mechanical ones. Use **`python lint.py --merged`** for the final check: it lints `built/` overlaid by `final/` — the exact set that ships.
2. **`python dedupe.py --selftest`** — verify the variant-vs-homograph classifier on known pairs before trusting it. (Not a pipeline step: the classifier is applied automatically by `add_notes.py` at delivery.) Spelling variants of one word collapse to a single card; genuine homographs (Hindi की/कि) BOTH stay and get a **part-of-speech-only hint** on the front — never the gloss, the card must still test recall. Where two homographs share a part of speech (माँ/मान, both nouns), give the `pos` its gender or set an explicit `hint`; lint flags any pair whose fronts would be identical.
3. **Sentence dedupe** — `lint.py` reports duplicate sentences. If agents reused sentences, run a small rewrite workflow: each duplicate's lower-rank (more frequent) card KEEPS the sentence, and the others get fresh ones written into the `final/` files.
4. **For Hindi: `python <skill>/assets/hindi-extras/nuqta.py final`** to preview, then the same command **`--write`** to apply. It restores ज़/फ़ dots wherever the romanization says z/f so TTS pronounces correctly. Without `--write` it changes nothing and says so.

## Phase 4 — Design (non-negotiable)

Use `card_css.py` EXACTLY as shipped — William approved this design and wants every deck to look like it. Parchment `#EFEDE6` / indigo ink `#1C2C4C` / marigold `#B07A16` (dark mode `#12151E` / `#E7E4DA` / `#DFAE55`); Avenir Next headwords, Iowan Old Style glosses; the word hanging from its rule (the shirorekha); rank line "N of TOTAL"; sentence in its panel on the back; native script faint at the foot. Do not redesign, "refresh", or substitute fonts. Read `references/design.md` for what each element means before making even small adaptations (e.g. what marigold may mark in a non-Hindi deck).

`anki.py` builds the note type FROM `card_css.py`, so the design has exactly one source of truth — never paste templates anywhere else.

Card markup comes from `markup.py`: the target word inside its sentence gets `<b class=tw>` (weight + hairline, never color); accent letters get `<span class=rx>` (marigold). The two signals never compete. All text is HTML-escaped before markup is applied. For Hindi, capitals MEAN retroflex, so `normalize_caps`/`normalize_headword` lowercase everything the native script doesn't justify — sentences genuinely start lowercase; never "fix" that.

## Phase 5 — Audio

`audio.py` — edge-tts, free, no key. Pick the voice with `edge-tts --list-voices | grep <lang>` (Hindi: `hi-IN-SwaraNeural`; for topic decks in English use an `en-US-*Neural` voice) and set `VOICE` in config.py.

- Run **`python preflight.py`** first — it checks config, template/field agreement, and that Anki is reachable, before the build spends thousands of TTS calls.
- `python audio.py --limit 20` is a smoke test; `--dry-run` reports without generating.
- **Filenames key on rank, never deck position** (`<prefix>_w_r00147.mp3`) — positions renumber, ranks don't.
- **`audio_stamps.json` records a hash of the text AND the voice each clip was spoken from** — edit any word or sentence, or change the voice, and exactly the affected clips regenerate on the next run.
- Feed the TTS the NATIVE form (`term` / `sent_native`: Devanagari, not romanization) — that's what the voice pronounces correctly. Clips are written straight into Anki's media folder (resolved from the running Anki, so any profile name works), 6 concurrent with backoff, and a failed clip never replaces a good one.
- **Tell the user once:** edge-tts sends each card's text to a Microsoft endpoint to be spoken, through an unofficial client. Fine for a frequency list; worth a mention if the deck contains anything private.

## Phase 6 — Deliver into Anki

Anki must be OPEN (AnkiConnect on localhost:8765; `open -a Anki`, poll `version`). Then:

1. **`python anki.py`** — creates/verifies the deck and the note type (11 fields + `Hint`, templates from `card_css.py`), then **reports the deck's options preset without changing it**. v3 creates no preset, edits none, and moves no deck between them: a new deck keeps Anki's **Default** preset and Default's rules, and an existing deck keeps whatever preset the user chose. **Never** set daily limits from the skill — Default is shared by several decks, so writing to it would re-pace all of them. If the user wants a different pace, tell them to set it in Anki's deck options (Deck ⚙ → Options), or to make a preset by hand and assign it there.
2. **`python add_notes.py`** — adds in ascending rank order (Anki's new-card order follows insertion). It refuses a batch whose ranks fall below what's already delivered, saves progress after every 100 notes, and leaves Anki's duplicate check ON. `--dry-run` first if you want to see the plan.
3. **Audit the LIVE deck, not the source files** — checklist in `references/pipeline.md`.
4. **Export a backup**: `python -c "import anki; anki.export('<path>.apkg')"` → send the .apkg to the user. This saved the Hindi deck when an AnkiWeb pull wiped the collection. Warn the user: if they sync after a build, the FIRST sync must be *Upload to AnkiWeb*, never Download.

If content changes after delivery, fix it in the `final/` files, re-run `audio.py`, then **`python refresh_fields.py --dry-run`** and, if the report looks right, `refresh_fields.py`. It overwrites card content from the batch files — any edit made by hand inside Anki that isn't also in the files is lost.

## Phase 7 — Report

Show the user real cards: render a preview HTML from live notes (pattern in `references/pipeline.md`) and send it with SendUserFile. Report: card count, audio count, verification catches worth knowing (they build trust), any deliberate oddities (lowercase sentences, homograph hints) so they don't read as bugs.

**Then archive the state that makes Phase 8 possible** — copy `manifest.json`, `audio_stamps.json`, `batches/candidates.json` and the whole `final/` directory somewhere durable (next to the .apkg), and save a memory file recording the deck, its note type, per-deck conventions chosen, and **where that archive lives**. The session scratchpad is disposable; without the archive a later extension has to rebuild everything from the live deck.

## Phase 8 — Extend an existing deck

1. Copy the skill's `scripts/` to a fresh `deck/`, and put the archived `final/`, `candidates.json`, `manifest.json` and `audio_stamps.json` alongside. Set config.py to the SAME deck, model, prefix, tag and voice as the original build, with `TOTAL` raised to the new size.
2. **`python add_notes.py --reconcile`** — rebuilds the manifest from the live deck (by tag), so a lost, stale or missing manifest is a non-event instead of the trigger for a duplicate flood. Do this even when you have the archived manifest: cards may have been deleted since.
3. New candidates: `python <skill>/assets/hindi-extras/extend.py --count N` (run from `deck/`) continues from `candidates.json`. Then Phases 2–6 for the new batches only.
4. Because `TOTAL` changed, the "N of TOTAL" line on existing cards is now stale — run `refresh_fields.py --only Rank` to correct every card's denominator.
5. In the Phase-6 live audit, check `is:new` for the notes THIS run added; on a deck already being studied the whole-deck version of that check is meaningless.

## Quality bar

Done means: every card has all fields; zero missing audio; no duplicate headwords except hinted homographs, each with a DISTINCT hint; no duplicate sentences; content verified by an independent adversarial pass; deck ordered most-useful-first; `lint.py --merged` clean; `preflight.py` green; .apkg backup delivered. The Hindi build hit all of these — match it.
