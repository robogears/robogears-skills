# AUDIT_BASELINE.md

last_run: 2026-07-21 · no-vcs (SKILL.md d22e8e7952bb · dedupe.py 3ec076186114, both pre-fix v0.1.3)
ids: DAT-undo-defused-names-658990ef, ERR-verify-poisoned-residual-05e99046, LIF-move-lock-stale-e5118beb, ERR-verify-churn-crash-52a8de84, LIF-undo-unlocked-race-70d51c6b, ERR-converged-false-positive-d845f5dc, FS-decmpfs-cloud-misclass-3a37f23d, ERR-missing-db-silent-88d123ae, QUA-doc-verify-cache-58601940, QOL-icloud-root-unguarded-6602fc1f, QUA-doc-refusal-subset-51f0165e, QUA-dual-restore-drift-b2182f89, QOL-nucleus-snapshot-copy-5b7ba086, QOL-no-persistent-cache-e14b06f9

All 14 findings above were fixed in v0.1.4 (2026-07-21); a re-run of /audit should
report them as FIXED. Audit report: ~/Documents/Random Claude Code Stuff/check-for-dupes-audit-2026-07-21.md
Regression tests pinning the fixes: scripts/test_dedupe.py (10/10 passing 2026-07-21).

| id | status | justification | approver | expires |
|----|--------|---------------|----------|---------|
