# teedaa

A **strictly read-only** folder auditor for macOS. Point it at any folder —
especially a giant Dropbox one — and it tells you exactly what's in there,
where the space goes, what's safely removable, and how it could be tidied,
**without touching a single file** until you explicitly approve a specific
plan. It never deletes anything, ever.

It is built for the hard case: a cloud folder with millions of files where
most are cloud-only placeholders. teedaa inventories all of it — sizes,
dates, content fingerprints — **without downloading a single byte**.

## What it does

- **Maps your storage** — an interactive treemap dashboard (click to zoom)
  plus a plain-English report: biggest folders, category breakdown, age
  timeline, quick wins.
- **Finds dead weight** — old installers, caches, partial downloads, stale
  temp files, crash logs, sync-conflict copies, empty-folder forests — each
  tiered 🟢 safe / 🟡 review / 🔵 look-first, with the evidence for every call.
- **Protects what matters** — never suggests touching anything inside a git
  repo, code project, app bundle, photo/music library, VM, database, or device
  backup. Old ≠ junk: a 1998 photo is treasure; a 1998 cache file is not.
- **Tells your photos from app junk** — real camera/phone captures vs icons,
  web images, and app assets, so a "consolidate my photos" suggestion only
  ever moves your actual pictures.
- **Flags sensitive files discreetly** — passwords, IDs, financial and medical
  documents are detected by name only, never opened, and never put in any
  cleanup plan.

## The one rule (rule zero)

teedaa is **read-only by default and never deletes anything, period.** The
only change it can ever make is *moving* files into a quarantine folder — and
only after you approve one specific plan, item by item, with a manifest and a
one-command undo. A quarantined file is never gone; emptying the quarantine is
something *you* do later, by hand.

It also **cannot** download your cloud files. At startup it flips an
OS-level switch (`setiopolicy_np`) that makes materializing a placeholder
*impossible* — any accidental read fails loudly instead of pulling gigabytes.

## Using it

Just ask Claude: **"/teedaa"**, *"audit this folder"*, *"what's taking up all
my space in ~/Downloads"*, or paste a folder path. Under the hood it runs a
small Python engine (stdlib only, no dependencies):

```bash
python3 scripts/teedaa.py scan   "<folder>"   # inventory (read-only, resumable)
python3 scripts/teedaa.py report "<folder>"   # markdown report + dashboard.html
python3 scripts/teedaa.py plan   "<folder>"   # risk-tiered proposals (executes nothing)
```

Reports and dashboards are written to `~/.cache/teedaa/reports/` — never
inside the folder you're auditing.

Only if you approve a specific plan does anything move:

```bash
python3 scripts/teedaa.py apply  "<folder>" --plan <file.json> --approve <plan_id>
python3 scripts/teedaa.py verify "<folder>"   # prove nothing was lost
python3 scripts/teedaa.py undo   "<folder>"   # put everything back
```

`apply` demands the plan's own id echoed back (`--approve`) — a deliberate
double gate so nothing can run by accident.

## Example (real run on ~/Downloads)

```
scan complete: 103 files in 10 folders · 7.5 GB · 0 errors
report: 652.8 MB looks like removable dead weight
  🟢 junk-safe    9 items  236.1 MB  (.DS_Store, Apple crash logs, installed-app .dmgs)
  🟡 junk-review  5 items  416.7 MB  (installers for apps not currently installed)
  🟡 photos       7 items  4.0 GB    (camera captures → Photos/YYYY/MM)
nothing moved — approve a plan to act
```

## Safety at a glance

| Guarantee | How |
|---|---|
| Never reads file contents | metadata only (names, sizes, dates, flags) |
| Never downloads cloud files | OS-level materialization kill switch |
| Never deletes | the only mutation is a rename into quarantine |
| Never moves without approval | per-plan `--approve <plan_id>` gate |
| Always reversible | manifest + `undo.sh` + engine `undo`, 30-day review |
| Never touches protected zones | git / bundles / libraries / backups excluded |
| Sensitive files stay private | detected by name, never listed, never planned |

## Status

v0.1.0. Audited (adversarial multi-agent review, all findings fixed),
36-test suite (synthetic fixtures only — tests never touch real data),
validated on a real 202.9 GB Dropbox folder of cloud-only placeholders with
zero downloads, and on real local junk. Not published to GitHub.
