# What changed in v2

v2 is `ankideckcreator` with every finding from the 2026-07-28 audit fixed
(0 Critical · 5 High · 24 Medium · 20 Low · 8 Suggestions). v1 is unchanged, so
the Hindi Core 2k build it produced can still be reproduced exactly.

Three structural changes underpin most of the fixes:

- **`scripts/config.py`** — one file holds every deck constant. v1 duplicated
  DECK/MODEL/PREFIX/TOTAL/VOICE across five scripts and enforced agreement with
  a comment; a single mismatched PREFIX pointed every note at audio that did not
  exist.
- **`scripts/record.py`** — one definition of a card record, with the documented
  field names canonical and the old Hindi names still accepted.
- **`scripts/preflight.py`** — checks config, template/field agreement and Anki
  reachability *before* a build spends thousands of TTS calls.

## High

| Finding | Fix |
|---|---|
| `QUA-schema-keys-drift-d9a330f5` — documented schema matched no script | `record.py` defines the schema; docs, prompt templates and all scripts use it; legacy keys aliased so old batch files still load |
| `QUA-stale-scaffold-hint-6ccd9fce` — scaffold shipped a stale design and no `Hint` field | `anki.py` imports CSS/FRONT/BACK from `card_css.py` and creates 12 fields incl. `Hint`; it verifies fields on an existing model and refuses rather than let AnkiConnect silently drop values |
| `DAT-manifest-endwrite-dupes-958910b9` — one crash + one re-run = mass duplicates | manifest flushed atomically after every chunk; `allowDuplicate` is now **false**; `--reconcile` rebuilds the manifest from the live deck |
| `ERR-nuqta-silent-noop-f1436396` — dot restoration silently did nothing, three ways | dry run is the default and says so; `--write` documented in SKILL.md and hindi.md; path resolves against the working directory; an empty scan is an error |
| `DAT-audio-clip-deletion-788372e1` — failed regeneration deleted good clips | synthesis goes to a temp file and `os.replace`s into position only when verified; only the temp file is ever removed; stamps flush during the run and in a `finally` |

## Medium

| Finding | Fix |
|---|---|
| `QUA-rank-double-suffix-934961ed` | template renders `{{Rank}}` alone; the field owns the whole phrase, from `config.TOTAL` |
| `DAT-default-preset-mutation-a012a981` | a failed clone aborts configuration; the deck's preset id is checked against Default before saving |
| `DAT-refresh-clobber-d4398a4d` | docstring states what it really does; `--dry-run` reports every field it would change; `--only` narrows it; `keep` filter added |
| `ERR-refresh-abort-deleted-bee6b69e` | deleted notes are detected up front and reported, never fatal |
| `DAT-lint-fix-capitalizes-7b383781` | the sentence-capitalizing block is gone |
| `SEC-html-injection-029c9ad1` | every field is HTML-escaped before markup is generated (order matters); `lint.py` flags markup in source content |
| `DEP-no-version-pins-4bd1e868` | `requirements.txt`, pinned, with the rank-shift rationale |
| `DAT-built-fallback-permanent-4f95b6dd` | loading from `built/` prints a warning naming the unverified batches |
| `DAT-out-of-order-resume-fbf7924e` | delivery refuses out-of-order ranks unless `--allow-unordered` |
| `DAT-nonatomic-writes-2cbd2016` | every state write is temp-file + `os.replace`; `--fix` only rewrites files it changed |
| `DAT-nuqta-substring-e6597346` | the blanket substring replace is gone; only the aligned word-by-word pass edits sentences |
| `QUA-prep-cch-double-34420539` | the ca-row rules use placeholders so they cannot consume each other's output |
| `QUA-danda-in-token-4fe16b8f` | letters-only Devanagari classes everywhere; tokens stripped of danda |
| `QUA-normcaps-alignment-a1294818` | alignment ignores danda/digit tokens and splits hyphens on both sides |
| `FS-hardcoded-media-path-9f2976fd` | media folder resolved from the running Anki (`getMediaDirPath`) |
| `QUA-no-copy-instruction-7d57c5d0` | Phase 0 opens with the copy-to-scratchpad command and says why |
| `QUA-nonhindi-transforms-ce4028c1` | `ACCENT_CODE` gates every Hindi transform; `None` for a plain deck |
| `QUA-extend-unspecified-4e6b6f56` | Phase 8 documents extension end to end; Phase 7 archives the state it needs |
| `DAT-prep-import-side-effect-9d9a6776` | `prep.py` runs behind a `__main__` guard |
| `QUA-first-letter-blindspot-d21eb154` | capital checks inspect every letter; `lint.py` compares retroflex counts per word |
| `DAT-stale-hint-baking-9489d53b` | hints recomputed over the full set on refresh; identical hints warned at delivery and flagged by lint |
| `DAT-extend-positional-resume-fed3fc34` | `extend.py` resumes from the persisted `candidates.json`, and warns if the live list has shifted |
| `QUA-missing-retroflex-unchecked-4c0ee672` | `retroflex_under` — the missing-mark direction v1 could not see |
| `QUA-gyan-jn-draft-3dc93a0e` | `jñ → gy` rule + spec line + exceptions entries |

## Low and Suggestions

All 28 applied. In brief: rank-less records are reported and skipped instead of
crashing (`lint`) or crashing mid-delivery (`add_notes`); CLI arguments are
validated so a swapped flag is an error, not a silent clean pass; `lint.py
--merged` lints the built/+final/ set that actually ships; the dedupe classifier
gained a `--selftest`, an anchored `ये` rule, a stop-only nasal rule, an honest
`skeleton` docstring, a real `_lead_word`, `are/was/were` stopwords, and a
nuqta-aware equality test; the stem matcher gained a second acceptance rule for
short shared stems (so `aanaa`/`aae` is caught, which v1 missed) while keeping
v1's rule intact — it still accepts `pataa`/`patthar`, a false match the audit
rated Low, because every rule strict enough to reject it also lost a dozen
ordinary conjugations; un-bolded targets are now REPORTED by lint instead, which
is the honest fix; `m̐`, word-final `ī` after a vowel, and
right-to-left schwa deletion are fixed in `prep.py`; nuqta reassembly keeps
punctuation in place, handles hyphenated compounds, and reports dots it cannot
justify; sentence audio is keyed on one shared rule so references and files
cannot disagree; the freshness stamp covers the voice; clips are generated only
for what actually ships; `extend.py` numbers batches from the highest existing
index and refuses to overwrite; AnkiConnect `False` returns are treated as
errors, an optional API key is supported, and `exportPackage` gets a longer
timeout; deck and model ids are recorded so a rename is caught; `refresh_fields`
can repair audio refs, `Rank` and `Hint`; dead code and unmarked Hindi-only
constants are gone; agent prompts carry a data-not-instructions line; Tatoeba
URLs are `https://`; audio prints progress and documents `--limit`; the retry
backoff no longer holds a concurrency slot; docs fixed for the manifest schema,
"lowest-rank keeps the sentence", the dedupe merge tier, the `is:new` check on a
studied deck, and prep's overshoot (2600, not 2200).

## Round 2 — bugs found in the fix pass itself

Three independent verifiers then attacked v2 (High findings, rewrite
regressions, and a literal read of the docs). They found real defects that this
version fixes; several were bugs the fix pass INTRODUCED, which is why they are
listed as plainly as the originals.

| Bug in v2's first cut | Fix |
|---|---|
| **`allowDuplicate: False` made genuine homographs undeliverable.** Anki's duplicate check compares the first field, and की/कि share it — the protection added for the manifest bug would have blocked the deck's own homograph feature | duplicates are allowed for exactly the cards whose headword genuinely collides in this deck (`_homograph`), and nothing else |
| **`invoke()` caught only `URLError`.** A read timeout or reset connection arrives bare, so it flew past every `except AnkiError` — reviving the notes-in-Anki-but-not-in-the-manifest route | `OSError` and decode errors are caught too, and the message points at `--reconcile` |
| **An existing note type's templates were never updated.** Extending a v1-built deck would keep v1's templates while v2 writes the full rank phrase → "147 of 2000 of 2000" | `ensure_model` compares the live templates and CSS against `card_css.py` and pushes them |
| **`lint` demanded a roman marker for ण**, which the spec writes as a plain "n" — a false positive on कारण, गुण, क्षण… | ण counts for the *sound* (first-consonant test) but not for the *marker* count |
| **Precomposed ड़ (U+095C) counted as zero retroflex**, inventing missing-mark warnings and letting a genuine capital be lowercased | all counting normalises to NFD first |
| **`lint` CANONICAL held two self-mappings** (`thi`→`thi`, `hue`→`hue`) — permanent, unfixable false positives | removed |
| **`lint --fix` truncated `davaaii` → `davaai`**, undoing prep.py's own corrected spelling | the final-vowel rule now understands the vowel-preceded convention instead of a fixed allowlist |
| **The new stem matcher lost ordinary conjugations** v1 had matched | v1's rule is kept AND the short-stem rule added — a union, so nothing regresses |
| **`nuqta` dotted a letter arbitrarily** where v1 declined as ambiguous; an unjustified dot aborted the remaining pairs | conversion requires exactly matching counts; findings are reported and the scan continues |
| **`dedupe` stopped collapsing आये/आए** (independent vowel before ये) | independent vowels join the lookbehind |
| **An explicit `hint` was discarded** unless the headword collided | it ships regardless; the pos-derived hint stays collision-gated |
| **`record.get` let an empty canonical key shadow a populated legacy one** | empty values fall through to the legacy key |
| **`refresh_fields` pushed a hint onto cards `add_notes` left hint-free** (it grouped without collapsing variants first) | both now collapse identically |
| **`lint` had dropped v1's non-ASCII check**, so ISO diacritics could ship | restored |
| **`lint --fix` still applied Hindi rules with `ACCENT_CODE = None`** | gated |
| `audio.py --limit` / `refresh_fields --only` crashed on a missing or bad value; `nuqta`'s dry run exited 1; `preflight` checked only the FRONT template; `nuqta`'s usage message printed the wrong paragraph | all fixed |

## Verification

Exercised on synthetic data covering every failing case named in the audit:
15/15 markup cases, 15/15 dedupe classifier pairs, 21/21 romanization cases,
7/7 nuqta cases, 12/12 round-2 regression cases, plus end-to-end lint and
delivery runs that caught every planted defect and confirmed a clean file stays
clean. Nothing was run against a live Anki collection — Anki was closed, so
every AnkiConnect path is reasoned-and-reviewed rather than executed. **Do a
`preflight.py` and an `add_notes.py --dry-run` against a scratch deck before the
first real build.**
