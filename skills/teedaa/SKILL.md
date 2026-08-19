---
name: teedaa
version: 0.1.0
description: >-
  teedaa (v0.1.0) — Full READ-ONLY audit of any folder — especially huge
  Dropbox/CloudStorage trees full of cloud-only placeholder files it inspects
  WITHOUT downloading a single byte — that maps exactly where the storage goes,
  finds dead weight (old installers, caches, partial downloads, stale temp
  files, crash logs, sync-conflict copies, empty-folder forests), protects
  git trees / app bundles / libraries / backups from ever being suggested,
  tells personal camera photos apart from app assets, and flags sensitive
  files discreetly. Delivers a beginner-readable report + an interactive HTML
  dashboard (click-to-zoom storage treemap, age timeline, quick wins) plus a
  permission-gated cleanup PLAN (move-only quarantine with manifest + undo).
  RULE ZERO: NOTHING is moved or touched without explicit user approval, and
  NOTHING is ever deleted, period. Use when the user says "/teedaa", "audit
  this folder", "clean up this folder", "what's taking up all my space",
  "what's in this folder", "tidy my Dropbox", or pastes a folder path asking
  what to do with it. NOT for pure duplicate-hunting as the main job (that's
  check-for-dupes — teedaa reports dupe overlap and hands off), NOT for
  auditing code projects (/audit), NOT a deleter — it cannot delete anything.
---

# /teedaa (v0.1.0)

You are auditing someone's digital life — possibly thirty years of it. Your
job: show them exactly what's in a folder, what it costs in space, what's
safely removable, and how it could be organized — while touching NOTHING
until they explicitly approve a specific plan. Their 1995 files may be the
most precious things they own. Their 2019 installers are not.

## Rule zero — non-negotiable (repeat VERBATIM to any subagent)

1. **Read-only by default.** `scan`, `report`, and `plan` never modify the
   target tree in any way.
2. **Nothing executes without explicit approval.** `apply` runs ONLY a plan
   file whose plan_id the user approved in conversation — and the engine
   demands the id echoed back (`--approve <plan_id>`), so even a slip in
   conversation cannot execute anything.
3. **Nothing is EVER deleted.** The only mutation in the entire engine is a
   same-volume rename into a quarantine/destination folder, with a durable
   manifest and an undo script. There is no delete code to call. Emptying a
   quarantine is a human act, outside this skill.
4. **Never read a cloud placeholder's bytes.** The engine arms an OS-level
   kill switch (`setiopolicy_np` — dataless materialization OFF) so a
   download is IMPOSSIBLE, not just avoided: any would-download op fails
   with EDEADLK and the engine records + routes around it via Dropbox's own
   local database.
5. **Sensitive files are never in any plan** and are reported discreetly —
   category counts and folder paths only; intimate media never gets
   filenames listed at all. Borderline items fail closed (treated as
   sensitive until the user looks).

## The engine

```bash
python3 ~/.claude/skills/teedaa/scripts/teedaa.py <subcommand> "<folder>" [flags]
```

Subcommands: `scan` (metadata-only index, resumable) · `report` (markdown +
dashboard HTML) · `plan` (proposals as JSON; executes NOTHING) · `apply`
(one approved plan; rename-only) · `verify` (prove nothing lost) · `undo`
(restore everything). Exit codes: `0` OK · `2` verification found real
problems · `3` refused/lock/safety · `64` usage error. Every subcommand
prints one `RESULT_JSON:` line (with `--json`, that is all stdout carries).
Do not re-implement any of its logic ad hoc — it encodes hard-won lessons
(below).

## Workflow

**1. Confirm the target.** Resolve the user's words to ONE absolute path.
The engine refuses `/`, system roots, bare `/Users` & `/Volumes`, and
`~/Library`. Whole-Dropbox and `~/Downloads` are fine — auditing them is
the product. If genuinely ambiguous, ask before scanning.

**2. Scan — in the background for big trees.**

```bash
python3 ~/.claude/skills/teedaa/scripts/teedaa.py scan "<folder>" --json
```

Local trees run ~20–60k entries/s; CloudStorage slower (expect minutes to
tens of minutes for hundreds of thousands of files). Interrupted scans
resume with the same command. `RESULT_JSON` reports `killswitch:true` —
if it ever says false on a cloud target the engine already refused; do not
work around it. `backfilled_files` = cloud-only folders inventoried from
Dropbox's records without opening them (working as designed).

**3. Report.**

```bash
python3 ~/.claude/skills/teedaa/scripts/teedaa.py report "<folder>" --json
```

Produces `report.md` + `dashboard.html` + `data.json` under
`~/.cache/teedaa/reports/` (never inside the audited tree). Send BOTH to
the user: the dashboard via SendUserFile (render) or as an Artifact page,
the markdown as the narrative. Walk through it beginner-readable (William's
standing preference): plain-English lead lines, jargon glossed, aggregates
not raw dumps — and LEAD with what was protected/excluded before what's
removable (trust first).

**4. Plan — and present the consent checklist.**

```bash
python3 ~/.claude/skills/teedaa/scripts/teedaa.py plan "<folder>" --json
```

Emits per-suggestion plan files (junk-safe 🟢, junk-review 🟡,
dormant-caches 🟡, photo-consolidation 🟡, screenshots 🟡, cold-archive 🟡 —
top-level items idle 3+ years → Archive/YYYY/, sensitive folders left in
place) with op counts,
sizes, evidence, and a `consent` level. Present each as a checkbox row:
what, how many items, how much space, where it goes, how to undo. 🔵/C-tier
findings are report-only — never planned, never pre-checked.

**5. Apply ONLY what the user explicitly approved — one plan at a time.**
Honor the friction ladder in `consent`:
- `simple` → a plain yes for that specific plan is enough.
- `explicit` → your confirmation question must NAME the counts and sizes
  ("Move 4,812 cache files (11.2 GB) to _Quarantine?").
- `typed` → ask the user to type the folder's name back before running.
Every confirmation states the blast radius AND the recovery ("nothing is
deleted — manifest + undo script, 30-day review window").

```bash
python3 ~/.claude/skills/teedaa/scripts/teedaa.py apply "<folder>" \
  --plan <plan-file.json> --approve <plan_id>
```

**6. Verify after any apply. Never skip.**

```bash
python3 ~/.claude/skills/teedaa/scripts/teedaa.py verify "<folder>"
```

Exit 2 = a moved file is at NEITHER location — tell the user not to delete
anything and investigate. `undo` (engine) or the generated `undo.sh` puts
everything back; collisions restore as `<name>.restored`, nothing is
overwritten.

**7. Report the outcome** — what moved, what was skipped as
changed-since-plan (that is a safety feature, not an error), where the
manifest lives, and that deleting the quarantine later is the user's act
(for cloud folders that is also what actually reclaims quota).

## Why it works this way (do not "optimize" these away)

- **The kill switch is OS-level on purpose.** Discipline-based "we just
  won't read placeholders" failed before: even a stat-adjacent code path
  (or this harness's own Read tool) can hang or trigger a download. With
  `setiopolicy_np` armed, the failure mode is a clean EDEADLK the walker
  catches, records as `dataless`, and backfills from nucleus.sqlite3.
- **SF_DATALESS wins over everything; st_blocks==0 is NEVER the test.**
  File Provider evicts via a dataless decmpfs type, so genuinely-local
  compressed files also show 0 blocks — that exact confusion once produced
  a would-download regression in a sibling skill (caught 2026-07-21 by
  adversarial review before real use).
- **The nucleus snapshot has a 45-second deadline.** An unbounded SQLite
  backup of Dropbox's hot DB livelocks during post-reboot re-indexing (two
  real runs slept 20+ minutes at 0.0% CPU). Deadline → file-copy fallback
  (db + -wal + -shm together, never `immutable=1`).
- **Poisoned hashes are refused by invariant, not heuristic.** A genuine
  SHA-256 determines file size, so one hash seen at two sizes is a sentinel
  — Dropbox has been observed doing this across dozens of files. Such
  hashes are never used for duplicate math and never planned against.
- **Old ≠ junk, structurally.** Any junk verdict on a pre-2005 file demotes
  one tier ("vintage flag"), pre-2015 installers are review-only
  (abandonware may be unobtainable now), pre-2010 logs are treated as
  possible personal history (ICQ-era chat logs are family gold).
- **Protection is outermost-root, ties go to protected.** One `.git` (a
  DIR or a worktree FILE — never read it), one `package.json`, one bundle
  extension makes the whole subtree exempt from suggestions. Junk inside a
  repo stays where it is; the sole exception is credential exact-names
  (`id_rsa`, `.env`, `.pem`) which are REPORTED (tagged in-code-tree) but
  still never planned.
- **`will` is never a sensitive token by itself** — the user is named
  William. The sensitive matcher is tokenized bigram-first with a
  document-extension gate, which also kills passport.js, LICENSE,
  Solidity `contract`, Keynote `.key` (>200 KB), and `eid` inside an Eid
  photo burst. False positives here erode the trust the whole report needs.
- **Changed-since-plan means SKIP.** Apply re-checks size before each
  rename; a file that grew or shrank since planning is left alone. Plans go
  stale; reality wins; re-plan.
- **The ops checksum is accident-armor, not security.** apply refuses a
  plan whose operations array doesn't match its recorded sha256 — protects
  against corrupt/hand-edited plan files executing the wrong moves.
- **Manifests live in-tree AND mirrored in ~/.cache** — but verify/undo
  accept a cache mirror only when its rows resolve under the audited root
  (a mirror from another folder must never fail this folder's verify;
  found live in this build's own tests).
- **No symlinks are ever created in Dropbox** (they sync as broken paths or
  worse). Organization uses real moves + manifests; "shortcut" requests get
  a `_Map.html` path index instead.

## Anti-patterns — do not do these

- Do not `du`, `find`, `md5`, open, preview, or QuickLook anything in a
  CloudStorage tree "just to check" — that is exactly the download the
  engine exists to prevent. Ask the engine; it already knows.
- Do not present logical size as disk usage on a placeholder tree — the
  headline is the logical/on-disk SPLIT.
- Do not call a filename match a duplicate. Same name ≠ same file (proven
  repeatedly on this user's data). Content claims come from hashes only.
- Do not summarize junk as one number — every family gets its plain-English
  name, count, size, tier, and 2–3 example names.
- Do not put sensitive file NAMES in the main report body, ever.
- Do not batch multiple plans into one approval, pre-check 🟡 rows, or
  treat "clean it up" as approval for a specific plan. One plan, one
  explicit yes, then apply.
- Do not delete anything, offer to delete anything, or script a delete for
  the user inside this skill's flow. Point at the quarantine and the
  30-day review instead.

## Failure modes

- `scan` refused: killswitch unavailable on a cloud target → this Mac
  can't guarantee no-download; do not proceed with ad-hoc tools.
- `scan` slow / seemingly stuck on CloudStorage → fileproviderd throttling;
  progress line shows the rate; interrupt + resume later is safe.
- `report`/`plan` refused: "scan is incomplete" → finish the scan first.
- `apply` refused: approval token mismatch / plan modified / different
  folder → regenerate with `plan`, get fresh approval. Never hand-edit a
  plan file to force it through.
- `apply` reports `failed` ops (exit 2) → cross-device or permission
  errors; nothing was copied or deleted; report which and why.
- `verify` exit 2 → a moved file is at neither location. STOP. Tell the
  user not to delete quarantine; investigate the manifest row.
- Dropbox mid-re-index (right after reboot) → nucleus is transiently
  incomplete; backfill and hashes may be partial. Re-run scan later rather
  than trusting a thin result.
- Lock contested → another teedaa run is live on that folder; wait, never
  steal.

## Before you deliver — final self-check

- [ ] Did anything in this session write into the audited tree besides an
      explicitly approved `apply`? (If yes: stop, tell the user exactly
      what and where.)
- [ ] Does the report lead with protected/excluded items, gloss every
      technical term, and keep sensitive names out of the body?
- [ ] Is every number from the engine's JSON (not re-derived by hand)?
- [ ] Was each applied plan individually approved at its consent level,
      and was `verify` run after?
- [ ] Did you offer the undo path and the 30-day review window?
