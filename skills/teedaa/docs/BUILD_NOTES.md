# teedaa build notes — decisions & deviations vs RESEARCH_DIGEST.md

## Item 2 (engine part 1)

- **No threading in v0.1.** Digest's own measurements show threads HURT on
  warm APFS and risk fileproviderd livelock; single-threaded is both simpler
  and faster. No `--threads` flag shipped at all — can be added later if a
  cold-HDD use case appears.
- **Index lives in `~/.cache/teedaa/index-<sha1[:12]-of-root>.sqlite3`** —
  never inside the scanned tree (rule zero: scan writes nothing there).
  `--index` overrides for tests.
- **Guardrail scope differs from check-for-dupes on purpose:** scan is
  read-only, so user-content roots (`~/Downloads`, whole Dropbox root) are
  ALLOWED — auditing them is the product. Refused: `/`, any system root
  directly under `/`, bare `/Users` + `/Volumes`, and `~/Library` (also
  auto-excluded when scanning `~`). `apply` will get the strict
  check-for-dupes-grade guardrail separately (item 8).
- **Kill switch policy:** if `setiopolicy_np` cannot arm on macOS, cloud
  targets are REFUSED (exit 3), local targets continue with a warning.
  Non-macOS: no File Provider, local-only allowed.
- **Resume bug caught by smoke test:** hardlink first-sight set is preloaded
  from the index on resume, otherwise re-walked subtrees double-count
  multi-link inodes (43 B → 49 B in the fixture). Regression test to land in
  item 9.
- **Bundle boundaries are NOT enforced in the walker.** The walker records
  the full tree (sizes must include bundle innards); protected/bundle
  classification happens in item 4 on the index. This deviates from
  check-for-dupes (which refuses to walk bundles) because teedaa needs their
  sizes for the treemap; rule zero keeps it safe (nothing is ever suggested
  inside them — classifier's job).
- Perf sanity: 8,270 files / 493 dirs / ~1 GB local in 0.73 s wall.

## Item 1 (scaffold)

- Trigger description routes dupe-hunting to check-for-dupes and code audits
  to /audit; teedaa self-describes as READ-ONLY + never-deletes.

## Usage-monitor fix (mid-loop, infra)

- ccusage 20.0.17 (daemon default) is broken on this Mac: darwin-x64 native
  binary missing. Daemon restarted with OVERNIGHT_CCUSAGE_VER=15.9.7
  (JS-only, verified working). Every check_usage call in this loop passes the
  same var. Worth porting into the overnightprotocol skill later.

## Item 4 decisions

- Classifiers live in scripts/classify.py (same-skill module imported by
  teedaa.py) to keep file sizes sane; cross-SKILL reuse still copies.
- mdls bundle fallback SKIPPED in v0.1 (classification is offline/index-only);
  the bundle-extension + structural channels cover the real cases. Noted as
  possible QoL later.
- Image dimension heuristics SKIPPED in v0.1 (scan never reads content, and
  placeholders have no local bytes); name/path/size/EXIF-free rules only.
- files.cls single column, precedence sensitive > protected > junk > photo;
  protected zone lives on dirs.prot (root id) so files inherit by join.

## Item 11 — /audit + fixes (2026-07-22)

14-agent adversarial audit (6 finder clusters + verification gate) found
0C / 8H / 6M / 12L / 9S — all 8 High confirmed real by the skeptic gate.
Report: docs/AUDIT-2026-07-22.md. ALL 8 High + all 6 Medium + all 12 Low
FIXED this pass; Suggestions triaged (most fixed, rest in ladder backlog).
Test suite 23 -> 33. Headline fixes:
- Dashboard stored-XSS: embedded JSON now escapes EVERY '<' to < (kills
  case-variant </SCRIPT>, <!--, <![CDATA[ at once); the one raw-innerHTML
  ext string now esc()'d.
- apply/undo path containment: was lexical normpath only (symlink-bypassable);
  now resolves the PARENT dir and rejects '..'/non-normalized/symlink-escape.
- Write-ahead manifest: apply fsyncs each row BEFORE the rename, so a crash
  can never leave a moved file with no record (verify was falsely all-clear).
- report/plan/verify/undo now arm the no-download kill switch (was scan/apply
  only; classify runs symlink probes).
- --rescan preserved the schema meta row (was wiping it -> next open dropped
  the whole fresh index).
- Lock reclaim TOCTOU closed (atomic rename-aside, not blind remove).
- Classifier: burst photos "IMG_1234 (1).jpg" no longer shadowed to junk by
  J8; RAW sidecars pair correctly; office macro-lock exts; .ips ROM-patch vs
  Apple-report; combining-mark intl keywords reachable; GCP-key regex not
  firing on digit timestamps; asset-zone tokenizes relative to root.

Baseline saved at AUDIT_BASELINE.md (35 finding IDs) so the next /audit diffs
FIXED.

## Item 12 — blind eval on real data (2026-07-22, READ-ONLY)

Local: scan+report on ~/.claude/projects (8,270 files / 951 MB) — clean run,
0.7s scan / 0.27s report, dashboard rendered + browser-verified.

Dropbox zero-download proof on ~/Library/CloudStorage/Dropbox/DJ Sets:
- 5 files, ALL cloud-only placeholders (SF_DATALESS, st_blocks=0), 202.9 GB
  logical (217,870,452,066 bytes).
- scan: killswitch armed=true, nucleus_used=true, hashes_filled=5 (every
  placeholder got its real SHA-256 from nucleus.sqlite3), 0 errors, 50s.
- PROOF: st_blocks stayed 0 and SF_DATALESS stayed set on all 5 AFTER the full
  scan+report — nothing was downloaded (a read would have jumped blocks and
  pulled up to 82 GB per file).
- DJ Sets folder contents unchanged: exactly the original 5 files, zero teedaa
  artifacts written into the tree (rule zero holds on real data).
- report.md + dashboard.html generated entirely from placeholder metadata.

## Real-junk classifier validation (2026-07-22, ladder cycle 2, READ-ONLY)

Ran full pipeline on ~/Downloads (103 files, 7.5GB) — first eval on genuinely
varied real content (prior evals were clean data). Every classifier family
fired correctly:
- jA:J1 — .DS_Store ×2, .localized (system droppings)
- jA:J5 — 3× VibeLight-YYYY-MM-DD-HHMMSS.ips (the .ips fix: Apple-report-
  shaped → tier A, not naive over-match)
- jA:J6 — Folderify/Stats .dmg (apps INSTALLED → tier A) vs jB:J6 —
  anki/AstroPixel/DDJ .dmg+.exe (NOT installed → tier B). Installer app-match
  working.
- ph:cert — Rosso_Corsa.dng (RAW near-proof); ph:likely — DSC00253.jpg,
  IMG_*.PNG/MOV/heic (camera names); ph:asset — hex/UUID/images.jpeg (web junk)
- Documents (10 pdf), music (3 mp3), md left as KEEP (correct)
- 0 sensitive (none in Downloads — no false positives)
Plans: 🟢 junk-safe 9 ops/247MB · 🟡 junk-review 5 ops/436MB · 🟡 photo-
consolidation 7 ops/4.3GB. NOTHING executed; ~/Downloads has zero teedaa
artifacts (rule zero holds on real local data too).
Note (not a bug): "Rosso Corsa.xmp" (space) did NOT pair with "Rosso_Corsa.dng"
(underscore) → jB:SC orphan — exact-stem pairing is correct; can't prove a
space/underscore variant is the same file. Fuzzy pairing would risk false
pairs; left as-is.

## Dashboard interaction QA (2026-07-22, READ-ONLY, no code change)

Exercised every interactive path in the browser on the real Downloads
dashboard (47 rects, 10 subfolders): treemap render, drill-down, breadcrumb
(3 segments), location.hash routing, back-to-root, all 3 lens toggles
(category/age/extension), legend spotlight toggle, hover tooltip show/hide,
reclaim-row zoom links (4), detail sections (junk/protected/cache/sensitive),
top-100 column sort (100 rows), age chart (5,608 ink px) + donut (20,185 ink
px), stat tiles (6). ZERO console errors. Subsystem verified clean — no fix
needed. (Age data validated: 2021-2026 spread, incl. a 2024 zero-byte file
counted correctly.)

## Triggering-accuracy eval (2026-07-22, 3 independent routers, majority vote)

Gave 3 blind routers ONLY the descriptions of teedaa + check-for-dupes +
audit and 13 realistic prompts. Result: 13/13 correct.
- teedaa fires on: "audit my Dropbox", "clean up Downloads", "what's in this
  folder", "tidy my dropbox", "what's eating my disk space", "organize my kid
  photos" (all unanimous except kid-photos 2/3).
- check-for-dupes correctly wins: "duplicate photos", "same video twice",
  "dedupe this folder" (teedaa did NOT false-grab any).
- audit correctly wins: "security bugs", "everything wrong with my codebase",
  "is my electron app safe" (all unanimous).
- "what time is it in Tokyo" → none (unanimous).
Description validated — no change needed. Disambiguation (NOT for dupes /
NOT for code) works.

## Scale stress test (2026-07-22, READ-ONLY, ~/Library/Caches)

First test above 8K files — validates the engine's real use case (millions).
~/Library/Caches: 142,226 files + 7,920 dirs, 11 GB logical.
- Throughput: 150,146 entries in 5.33s wall = ~28,000 entries/s (digest claim
  20-60k/s — in range for a cold-ish deeply-nested small-file tree).
- Peak RSS: 140 MB (bounded — streaming walk, frows flushed every 20K rows,
  frontier-only dir stack; does NOT grow linearly with file count).
- Index: 15 MB for 150K entries (~100 B/file; digest ~63 B/file ballpark).
- Errors: 12 permission-denied cache files — recorded, scan completed clean.
- RESUME-AT-SCALE: reset 3,000 deepest dirs to 'pending' + dropped children
  (simulated crash), re-ran → recovered to 142,226 files, interrupted=False.
  (The ~690-file delta vs the pre-crash count is real cache churn between
  runs — Caches is live; the scan correctly reflects current reality.)
Guardrail note: ~/Library/Caches is ALLOWED (only bare ~/Library is refused).
