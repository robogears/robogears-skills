---
name: check-for-dupes
version: 0.1.5
description: >-
  check-for-dupes (v0.1.5) — Find and safely quarantine duplicate FILES in a folder by verifying actual
  file CONTENT (SHA-256), never just the filename — then prove no data was
  lost with a final security check. Use this whenever the user asks to find,
  remove, or clean up duplicate files/photos/videos in a folder, says things
  like "I think I have the same file twice", "dedupe this folder", "clean up
  these copies", mentions files ending in " (1)", " (2)", " copy", or wants to
  merge repeated photo exports (iCloud/Google Takeout/WhatsApp dumps). Also
  use it on Dropbox CloudStorage folders full of cloud-only placeholder files
  — it identifies those WITHOUT downloading them. Duplicates are MOVED to a
  "Duplicates" folder (never deleted) with a manifest and undo script. NOT for
  deduplicating lines inside a file, database records, or code blocks.
---

# check-for-dupes (v0.1.5)

Safely deduplicate a folder: identify true duplicates by content, move them to
a `Duplicates` folder, and run a security check proving nothing was lost.

Everything runs through the bundled engine — do not re-implement its logic ad
hoc; it encodes hard-won lessons (see "Why it works this way" below):

```bash
python3 ~/.claude/skills/check-for-dupes/scripts/dedupe.py <subcommand> "<folder>" [flags]
```

Subcommands: `scan` (dry run), `move` (quarantine), `verify` (security check),
`where` (per-file truth: list every byte-identical copy outside quarantine),
`heal` (recover wrongly-quarantined uniques), `undo` (restore everything). Each
prints a human summary plus a final `RESULT_JSON:` line you can parse.

**`where` — answering "is this file in Duplicates REALLY a duplicate?"**

```bash
python3 .../dedupe.py where "<folder>"                    # audit every quarantined file
python3 .../dedupe.py where "<folder>" "IMG_1234.mp4" …   # just these names
```

For each file it reports a verdict — TRUE DUPLICATE (with the actual outside
copies, found by content so renamed twins still match), NOT A DUPLICATE
(orphan — content exists nowhere outside; `heal` restores it), or UNVERIFIABLE
(placeholder/sentinel hash) — and cross-checks the manifest's recorded keeper
("keeper exists but is NOT identical" is the fingerprint of a pre-v0.1.3
sentinel-hash mis-file). Read-only. Exit 0 only when everything reported is a
true duplicate. Use it whenever the user doubts a quarantined file, before
deleting any `Duplicates` folder, and to triage a quarantine made by an older
engine version.

**Exit codes** (the same for every subcommand): `0` = OK (verify: clean, or only
inconclusive/still-syncing — nothing proven wrong); `2` = verify found real
problems (orphans, invalid placeholder-hash files, unreadable quarantined files,
or residual duplicates); `3` = refused/bad target; `64` = command-line usage
error. So exit `2` unambiguously means "verify found something wrong" — a typo
now exits `64`, not `2`.

**Flags on every subcommand:** `--json` (machine mode: only the `RESULT_JSON`
line on stdout, all human text on stderr), `--quiet` (suppress progress),
`--recent-secs N` (in-flight guard, default 120). `move` also takes
`--dry-run` (alias for `scan`) and `--max-passes N`.

## Workflow

**1. Confirm the target folder.** Resolve the user's words to one absolute
path. If genuinely ambiguous, ask. The script refuses broad/dangerous targets
(`/`, any top-level directory, `~`, `~/Desktop`, `~/Documents`, `~/Downloads`,
`~/Pictures`, `~/Movies`, `~/Music`, `~/Library`, `~/Public`, the Dropbox
root, the iCloud Drive root, a whole `/Volumes/*` drive, and a folder that is
itself named `Duplicates`) — point at a specific content folder. Pass an absolute path; a
folder whose name starts with `-` must be given as `./-name`.

**2. Recon, then decide the layout question.** Check whether the target
contains subfolders (ignore any existing `Duplicates` folders and hidden
dirs). If it does — even nested several levels deep — and the user hasn't
already said what they want, ask via AskUserQuestion:

- **One `Duplicates` folder at the top** (`--scope single`, recommended
  default): duplicates are matched across the whole tree — a photo in
  `2021/June` identical to one in `Camera Roll` counts as a dupe, and one
  copy of everything survives somewhere in the tree.
- **A `Duplicates` folder inside each subfolder** (`--scope per-folder`):
  duplicates are only matched within their own folder; the same photo in two
  different subfolders is left alone.

If the folder is flat, skip the question and use `single`. If you cannot ask
(running non-interactively), default to `single` and say so in your report.

**3. Scan first (dry run), report before moving.**

```bash
python3 .../dedupe.py scan "<folder>" --scope single --show 10
```

Report the numbers in plain English before touching anything: how many
duplicate groups, how many files would move, how much space, and how many
same-name-but-different-content families were detected and protected. If the
scan shows `recently-modified` skips, tell the user files are still arriving
(active import/sync) and results will be partial.

**Use the SAME `--scope` for every step.** Whatever you chose in step 2, pass it
to `scan`, `move`, and (optionally) `verify`. The scope is recorded in each
manifest, so `verify`/`undo` auto-detect it if you omit `--scope` — but be
consistent to avoid confusion.

**4. Move.**

```bash
python3 .../dedupe.py move "<folder>" --scope single
```

This loops (scan → move → rescan) until a pass moves nothing, because syncing
folders mint new copies mid-run. Files are *renamed* into `Duplicates` —
never copied, never deleted, no file contents are read that weren't already
local. The manifest is written durably **as each pass completes**, so an
interrupt (Ctrl-C, sleep, crash) still leaves every already-moved file fully
undoable — the run reports `interrupted=true`; just re-run `move` to finish.
Each `Duplicates` folder gets `_manifest.csv` (what moved, which original it
duplicates, its hash, the scope), `_UNDO_restore_all.sh`, and `_README.txt`.
If it reports `converged=false`, an import is actively racing you (or some
moves `failed`) — tell the user and offer to re-run later.

**5. Verify — the security check. Never skip this.**

```bash
python3 .../dedupe.py verify "<folder>"      # auto-detects the recorded scope
```

Proves two things: every quarantined file still has a byte-identical original
outside `Duplicates` (zero "orphans"), and no duplicate groups remain outside.
**Exit code 2 = a real problem** (orphans, an unreadable quarantined file, or
residual duplicates) — investigate before reporting success. Exit 0 with
`needs_reverify:true` in the JSON is NOT a clean bill of health: it means some
cloud files' hashes aren't indexed yet (`syncing`) or an original may be hiding
among not-yet-indexed files of the same size (`inconclusive`) — re-verify later
and do NOT delete `Duplicates` until a later verify comes back fully clean.

**5a. If verify reports CONFIRMED ORPHANS — heal them.** An orphan is a
quarantined file whose exact content exists NOWHERE outside `Duplicates` —
usually a unique file a previous/foreign dedup pass mis-filed (e.g. via a stale
hash). Deleting `Duplicates` would lose it. Recover automatically:

```bash
python3 .../dedupe.py heal "<folder>" --dry-run   # preview: what would come back
python3 .../dedupe.py heal "<folder>"             # restore one copy of each unique
```

`heal` re-runs the same content analysis, and for each distinct orphaned
content restores ONE copy to where it belongs (the tree root for `single`
scope, the owning subfolder for `per-folder`) while leaving its true duplicates
in quarantine. It **never deletes**, is idempotent (safe to re-run), logs each
recovery in `Duplicates/_heal_log.csv`, and deliberately does NOT touch
`inconclusive`/`syncing` files (those may just be un-indexed — re-verify later).
After healing, run `verify` again; it should now pass. Report how many unique
files you rescued — this is a real save, not routine cleanup.

**6. Report.** Lead with the outcome, keep it beginner-readable (bullets,
sizes in GB, jargon glossed). Include: files moved and space, what was
deliberately NOT moved (same-name-different-content families, skipped
in-flight files), any files `heal` rescued, the verify verdict, where the
manifest/undo live, and — for cloud folders — that deleting `Duplicates` later
is what actually reclaims quota; the move alone doesn't. Offer to re-run if an
import was active.

## Why it works this way (do not "optimize" these away)

- **Same filename ≠ same file.** Photo libraries routinely hold `IMG_0452.PNG`
  from 2020 and `IMG_0452 (1).PNG` from 2022 that are entirely different
  pictures. Only content hashes may decide — never names, never Spotlight/EXIF
  metadata (real case: two videos with identical metadata but different
  content; metadata dedup would have destroyed one).
- **Cloud placeholders lie.** In Dropbox CloudStorage folders, most files can
  be 0-block placeholders: `du` under-reports ~100×, and *reading one triggers
  a download* (a 3 GB-looking folder was really 290 GB). The engine never
  reads placeholders — it pulls Dropbox's own recorded SHA-256 from its local
  sync DB (`~/.dropbox/instance1/sync_fp/nucleus.sqlite3`). Renames are
  metadata-only: thousands of "moves" complete in seconds, zero downloads.
- **The cloud DB can be stale.** A locally-modified file's DB hash lags
  reality (real case: two videos the DB called identical differed in 20
  trailing bytes). Rule: locally-present bytes are always re-hashed for real;
  the DB is only trusted for cloud-only files, where it is authoritative by
  construction. This is why the engine's identity function is not negotiable.
- **The cloud DB can also *lie* — placeholder-hash poisoning.** Dropbox has been
  observed recording ONE sentinel SHA-256 for dozens of unrelated cloud-only
  files spanning many byte-sizes (real case: one hash across 89 files / 18 sizes,
  from 4.6 MB photos to 496 MB videos). Trusting it would quarantine 88 unrelated
  files against a bogus 4.6 MB "twin" — and `verify` would be fooled too, since
  every quarantined file's fake hash matches the survivor's fake hash. The guard
  is an invariant, not a heuristic: a genuine SHA-256 uniquely determines file
  size, so any hash seen at **more than one size** across the corpus is provably
  not a content hash. Such "poisoned" hashes are refused everywhere — the files
  are never quarantined (reported and left in place), and `verify` FAILS (exit 2)
  on any quarantined file still carrying one. On real data this flagged exactly
  the 1 sentinel among 44,193 hashes: zero false positives, so it is always on.
- **Live folders race you.** Active imports mint new `(1) (2)` copies
  mid-run. Hence: skip files modified <2 min ago, loop until convergence,
  re-check afterwards. `move`, `heal`, and `undo` all hold a single-writer
  lock (PID-checked, mtime refreshed each pass) so two mutating runs can
  never race the same tree; a lock owned by a live process is never stolen.
- **Never overwrite, never orphan.** Name collisions inside `Duplicates` get
  a ` [dupN]` suffix; a file is only moved if its keeper exists at that
  instant; verify() looks cloud-only quarantined files up in the *Duplicates
  folder's own* DB records (looking in the parent's records produces false
  orphan alarms).
- **Keeper choice:** cleanest name wins — no copy-suffix beats ` (1)`/` copy`,
  then lowest number, then shallower path, then shorter name.
- **Reversibility is durable.** The manifest is flushed (with `fsync`) at the
  end of each pass and on interrupt, so a crash mid-run never strands files
  without a way back. `undo` refuses any manifest row that would write outside
  the tree (a tampered manifest can't plant a file elsewhere), and only retires
  a manifest once every row is truly restored.
- **Boundaries are guarded.** A `Duplicates` folder that is a symlink (or
  resolves outside the tree) is refused, not followed; the guardrail refuses
  broad/system targets and whole external volumes; macOS package bundles
  (`.photoslibrary`, `.app`, …) are never walked into as if they were folders.

## Caveats to tell the user

- **Live Photos / paired files.** The engine matches by file content only and
  has no concept of a Live Photo pair (a `.HEIC` still + its `.MOV`). If one
  half is a byte-identical duplicate of another photo's half, it can be
  quarantined on its own — no content is lost, but the pairing in Photos could
  break. Mention this before deduping a Live Photo library, and prefer
  re-importing from Photos over deleting `Duplicates` for such libraries.

## Failure modes

- `verify` exits 2 with CONFIRMED orphans → do NOT delete anything; run `heal`
  (step 5a) to restore the unique files automatically, then re-verify.
- `verify` reports `inconclusive` / `needs_reverify` → nothing is provably
  wrong: the original likely sits among outside files whose cloud records
  haven't been indexed yet (an unidentified same-size file exists). This is
  routine right after big imports — Dropbox evicts fresh files to the cloud
  before publishing their hashes. Tell the user to re-run verify later; do
  not call these lost, and do not delete Duplicates until a later verify
  comes back clean.
- `verify` reports `unreadable` quarantined files → these DO exit 2; a
  quarantined file that is present but can't be read is a real problem, not
  sync lag. Investigate that file specifically.
- `verify` reports `poisoned` / "INVALID placeholder-hash" files → exit 2, a
  real problem: Dropbox recorded one content hash at several different sizes, so
  those quarantined files have no provable original. Do NOT delete `Duplicates`.
  `heal` cannot pick a safe original for them (it reports them as
  `still_poisoned`); run `undo` to restore them by manifest, then let the files
  download (materialize) and re-run `scan`/`move` so they hash for real. `scan`
  and `move` already refuse to quarantine such files, reporting them under
  `placeholder_hash_files` and leaving them in place. Poisoned files that were
  never quarantined and sit outside are reported by `verify` under the neutral
  `outside_placeholder_hash_files` counter — they do NOT fail verify and are
  NOT counted as residual duplicates (their "duplicate-ness" is exactly what
  the bogus hash cannot prove).
- `move` not converging → active sync (or some moves `failed`, shown in the
  summary); report partial success, offer re-run.
- Dropbox folder but the DB is missing/unreadable → the engine prints a
  "proceeding with locally-present files only" warning and continues; cloud-only
  files are skipped as `not-locally-present`. Say so and offer to re-run once
  Dropbox is running.
- Huge all-local folders → hashing is disk-bound; warn it may take minutes,
  run in background. Progress is time-based on stderr (files + bytes hashed +
  cloud lookups). A persistent hash cache (`~/.cache/check-for-dupes/`) lets
  later passes and re-runs of `scan`/`move` reuse work; `verify` deliberately
  ignores it and re-hashes everything, so the security check stays independent
  of the machinery it is checking.
