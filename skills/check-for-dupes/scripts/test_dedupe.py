#!/usr/bin/env python3
"""Regression tests for check-for-dupes (dedupe.py).

Runnable two ways:
    python3 -m pytest test_dedupe.py        # if pytest is installed
    python3 test_dedupe.py                   # plain stdlib fallback runner

These pin the fixes made in v0.1.4 (see AUDIT_BASELINE.md):
  * csv_safe / csv_unsafe round-trip and undo of defused ('-' '+' '=' '@') names
  * verify's neutral counter for outside placeholder-hash files (no false alarm)
  * Lock mtime-refresh + PID-liveness stale reclaim
  * manifest retirement blocked when a row is unaccounted for
  * heal of a synthetic orphan
  * --json stdout stays machine-clean for every subcommand

Fixtures are built under a throwaway temp tree (never inside Dropbox/iCloud).
Fresh files are hashed with --recent-secs 0 so the in-flight guard doesn't skip
them.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DEDUPE = os.path.join(HERE, "dedupe.py")
sys.path.insert(0, HERE)
import dedupe  # noqa: E402


# ----------------------------------------------------------------- CLI helpers
def run(*args):
    return subprocess.run([sys.executable, DEDUPE, *args],
                          capture_output=True, text=True)


def run_json(*args):
    """Run a subcommand in --json mode and assert stdout is exactly one clean
    RESULT_JSON line. Returns (returncode, parsed_dict)."""
    p = run(*args, "--json", "--recent-secs", "0")
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout not machine-clean for {args}: {p.stdout!r}"
    assert lines[0].startswith("RESULT_JSON: "), lines[0]
    return p.returncode, json.loads(lines[0][len("RESULT_JSON: "):])


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def newtree():
    return tempfile.mkdtemp(prefix="cfd_test_")


# ----------------------------------------------------------------- unit: csv
def test_csv_safe_unsafe_roundtrip():
    # the encoding is injective: genuine leading-quote names are quote-doubled,
    # so a real "'-x.jpg" can never collide with a defused "-x.jpg".
    for name in ("-test.jpg", "+9715551234.jpg", "=calc.png", "@2x.png",
                 "\tweird", "\rcarriage", "'quoted", "'-ambiguous.jpg"):
        safe = dedupe.csv_safe(name)
        assert safe[0] == "'" and safe == "'" + name, f"{name!r} was not defused"
        assert dedupe.csv_unsafe(safe) == name, f"{name!r} did not round-trip"
    # untouched values pass through unchanged
    for name in ("normal.jpg", "photo (1).png", "a-b-c.txt"):
        assert dedupe.csv_safe(name) == name
        assert dedupe.csv_unsafe(name) == name
    # a LEGACY row (old writer, genuine quote + non-trigger) is left untouched
    assert dedupe.csv_unsafe("'legacy.jpg") == "'legacy.jpg"


def test_st_is_local_dataless_wins():
    """File Provider evicts via a dataless decmpfs type, so real cloud
    placeholders carry UF_COMPRESSED too — SF_DATALESS must win or the tool
    would hash (download!) every cloud file."""
    from types import SimpleNamespace as NS
    D, C = dedupe.SF_DATALESS, dedupe.UF_COMPRESSED
    assert dedupe.st_is_local(NS(st_blocks=0, st_flags=D | C)) is False
    assert dedupe.st_is_local(NS(st_blocks=0, st_flags=D)) is False
    assert dedupe.st_is_local(NS(st_blocks=8, st_flags=D)) is False
    assert dedupe.st_is_local(NS(st_blocks=0, st_flags=C)) is True
    assert dedupe.st_is_local(NS(st_blocks=8, st_flags=0)) is True
    assert dedupe.st_is_local(NS(st_blocks=0, st_flags=0)) is False


# ----------------------------------------------- unit: poisoned + scan_quarantine
class _StubScanner:
    def __init__(self, outside):
        self._outside = outside

    def collect(self):
        return list(self._outside), Counter(), []


def test_poisoned_hashes():
    items = [{"hash": "AA", "size": 100}, {"hash": "AA", "size": 200},
             {"hash": "BB", "size": 50}, {"hash": "BB", "size": 50}]
    assert dedupe.poisoned_hashes(items) == {"AA"}


def test_scan_quarantine_excludes_poison_from_residual():
    root = newtree()
    try:
        # Two OUTSIDE files sharing one hash at DIFFERENT sizes = poisoned
        # (Dropbox sentinel). They must NOT be counted as residual duplicates.
        outside = [
            {"hash": "AA", "size": 100, "rel": "x1.bin", "dir_rel": "."},
            {"hash": "AA", "size": 200, "rel": "x2.bin", "dir_rel": "."},
        ]
        q = dedupe.scan_quarantine(root, "single", _StubScanner(outside), None)
        assert q["outside_placeholder_hash_files"] == 2, q
        assert q["residual"] == {}, q
        # verify would exit 0: no residual, no records => no problems.

        # A genuine same-size duplicate DOES surface as residual.
        outside2 = [
            {"hash": "BB", "size": 50, "rel": "y1.bin", "dir_rel": "."},
            {"hash": "BB", "size": 50, "rel": "y2.bin", "dir_rel": "."},
        ]
        q2 = dedupe.scan_quarantine(root, "single", _StubScanner(outside2), None)
        assert q2["outside_placeholder_hash_files"] == 0, q2
        assert len(q2["residual"]) == 1, q2
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------- unit: Lock
def test_lock_touch_and_stale_reclaim():
    root = newtree()
    try:
        lock = dedupe.Lock(root)
        with lock:
            assert lock.held
            assert os.path.exists(lock.path)
            # touch refreshes mtime
            old = time.time() - 10_000
            os.utime(lock.path, (old, old))
            lock.touch()
            assert os.path.getmtime(lock.path) > old + 5000

        # A lock file owned by a DEAD pid is stale regardless of mtime.
        with open(lock.path, "w") as f:
            f.write("999999 0\n")     # pid that is not running
        assert dedupe._pid_alive(999999) is False
        assert lock._is_stale() is True

        # A lock owned by THIS (alive) process with a fresh mtime is NOT stale.
        with open(lock.path, "w") as f:
            f.write(f"{os.getpid()} {int(time.time())}\n")
        os.utime(lock.path, None)
        assert lock._is_stale() is False

        # Alive owner but ancient mtime: a live run refreshes its lock, so we do
        # NOT reclaim (must not steal a legitimately long run).
        old = time.time() - (dedupe.STALE_LOCK_SECS + 1000)
        os.utime(lock.path, (old, old))
        assert lock._is_stale() is False
        os.remove(lock.path)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------- integration: scan/move/verify/undo
def test_full_cycle_with_special_names():
    root = newtree()
    try:
        # Short keeper names (root, depth 0) beat the longer trigger-named copies
        # on the keeper ranking, so the trigger-named files are the ones MOVED —
        # exercising csv_safe defusing of both stored_as and original_relpath.
        write(os.path.join(root, "k1.bin"), b"AAAA-content")
        write(os.path.join(root, "-test.jpg"), b"AAAA-content")
        write(os.path.join(root, "k2.bin"), b"BBBB-content")
        write(os.path.join(root, "+9715551234.jpg"), b"BBBB-content")
        write(os.path.join(root, "k3.bin"), b"CCCC-content")
        write(os.path.join(root, "=calc.png"), b"CCCC-content")
        write(os.path.join(root, "k4.bin"), b"DDDD-content")
        write(os.path.join(root, "@2x.png"), b"DDDD-content")
        # nested duplicate (normal names) to exercise tree walking
        write(os.path.join(root, "sub", "a", "deep.bin"), b"NESTED")
        write(os.path.join(root, "top.bin"), b"NESTED")
        # a unique file (never moves) and an empty file (skipped)
        write(os.path.join(root, "unique.bin"), b"unique-bytes")
        write(os.path.join(root, "empty.bin"), b"")

        specials = ["-test.jpg", "+9715551234.jpg", "=calc.png", "@2x.png"]

        # scan (dry run)
        rc, res = run_json("scan", root, "--scope", "single")
        assert rc == 0
        assert res["files_to_move"] == 5, res     # 4 specials + 1 nested/top pair
        assert res["placeholder_hash_files"] == 0

        # move
        rc, res = run_json("move", root, "--scope", "single")
        assert rc == 0 and res["converged"] is True, res
        assert res["moved"] == 5, res
        assert res["failed"] == 0

        dup = os.path.join(root, "Duplicates")
        manifest = os.path.join(dup, "_manifest.csv")
        assert os.path.exists(manifest)
        # the special-named files were physically moved out of the root
        for s in specials:
            assert not os.path.exists(os.path.join(root, s)), f"{s} not moved"
        # keepers remain
        for k in ("k1.bin", "k2.bin", "k3.bin", "k4.bin", "unique.bin"):
            assert os.path.exists(os.path.join(root, k))
        # empty file untouched and never quarantined
        assert os.path.exists(os.path.join(root, "empty.bin"))

        # prove the manifest actually defused the trigger-named rows
        with open(manifest) as f:
            body = f.read()
        assert "'-test.jpg" in body, "expected defused '-test.jpg in manifest"
        assert "'+9715551234.jpg" in body
        assert "'=calc.png" in body
        assert "'@2x.png" in body

        # verify -> clean
        rc, res = run_json("verify", root)
        assert rc == 0, (rc, res)
        assert res["confirmed_orphans"] == 0
        assert res["residual_duplicate_groups"] == 0
        assert res["ok"] is True
        assert res["outside_placeholder_hash_files"] == 0

        # undo via the ENGINE (fixed reader) -> every special name restored EXACTLY
        rc, res = run_json("undo", root)
        assert rc == 0, res
        assert res["restored"] == 5, res
        for s in specials:
            assert os.path.exists(os.path.join(root, s)), f"{s} not restored"
        assert os.path.exists(os.path.join(root, "sub", "a", "deep.bin"))
        # manifest retired only after a fully-accounted restore
        assert not os.path.exists(manifest)
        assert os.path.exists(manifest + ".restored")
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_undo_generated_script_restores_special_names():
    """The generated _UNDO_restore_all.sh (fallback path) must also round-trip
    defused names. We force the fallback by pointing its ENGINE at a missing
    path, then run it directly."""
    root = newtree()
    try:
        write(os.path.join(root, "k.bin"), b"payload-1")
        write(os.path.join(root, "-dash.bin"), b"payload-1")
        run_json("move", root, "--scope", "single")
        dup = os.path.join(root, "Duplicates")
        script = os.path.join(dup, "_UNDO_restore_all.sh")
        assert os.path.exists(script)
        with open(script) as f:
            text = f.read()
        # neutralize the engine hand-off so the embedded fallback runs
        text = text.replace(dedupe.ENGINE_PATH, "/nonexistent/dedupe.py")
        with open(script, "w") as f:
            f.write(text)
        assert not os.path.exists(os.path.join(root, "-dash.bin"))
        p = subprocess.run(["bash", script], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        assert os.path.exists(os.path.join(root, "-dash.bin")), \
            f"fallback did not restore defused name: {p.stdout}\n{p.stderr}"
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_manifest_not_retired_when_row_unaccounted():
    root = newtree()
    try:
        write(os.path.join(root, "a.bin"), b"same-bytes")
        write(os.path.join(root, "bb.bin"), b"same-bytes")
        run_json("move", root, "--scope", "single")
        dup = os.path.join(root, "Duplicates")
        manifest = os.path.join(dup, "_manifest.csv")
        # find the quarantined data file and DELETE it (simulate a stranded row:
        # not in quarantine, and not back at its origin either).
        moved = [n for n in os.listdir(dup)
                 if not n.startswith("_") and not n.startswith(".")]
        assert moved, "nothing was quarantined"
        os.remove(os.path.join(dup, moved[0]))

        rc, res = run_json("undo", root)
        assert rc == 0
        # the row could not be accounted for, so the manifest must survive —
        # and the loss must be COUNTED, never a silent 0/0/0 summary
        assert res["unaccounted"] == 1, res
        assert os.path.exists(manifest), "manifest wrongly retired past a lost row"
        assert not os.path.exists(manifest + ".restored")
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_heal_recovers_synthetic_orphan():
    root = newtree()
    try:
        write(os.path.join(root, "orig.bin"), b"orphan-content")
        write(os.path.join(root, "dup.bin"), b"orphan-content")
        run_json("move", root, "--scope", "single")
        dup = os.path.join(root, "Duplicates")
        # delete the surviving OUTSIDE keeper -> the quarantined copy is now an
        # orphan (its content exists nowhere outside Duplicates).
        keeper = "dup.bin" if os.path.exists(os.path.join(root, "dup.bin")) else "orig.bin"
        os.remove(os.path.join(root, keeper))

        rc, res = run_json("verify", root)
        assert rc == 2, (rc, res)
        assert res["confirmed_orphans"] >= 1, res

        rc, res = run_json("heal", root)
        assert rc == 0 and res["recovered"] == 1, res

        rc, res = run_json("verify", root)
        assert rc == 0, (rc, res)
        assert res["confirmed_orphans"] == 0
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_json_stdout_clean_all_subcommands():
    root = newtree()
    try:
        write(os.path.join(root, "a.bin"), b"dup")
        write(os.path.join(root, "bb.bin"), b"dup")
        # run_json itself asserts stdout is a single clean RESULT_JSON line
        run_json("scan", root)
        run_json("move", root)
        run_json("verify", root)
        run_json("where", root)
        run_json("heal", root, "--dry-run")
        run_json("undo", root)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_guardrail_refuses_icloud_and_home():
    # iCloud Drive root and other broad targets exit 3 (no RESULT_JSON).
    icloud = os.path.expanduser(
        "~/Library/Mobile Documents/com~apple~CloudDocs")
    if os.path.isdir(icloud):
        p = run("scan", icloud)
        assert p.returncode == 3, p.stdout + p.stderr
    home = os.path.expanduser("~")
    p = run("scan", home)
    assert p.returncode == 3


def test_lock_exit_ownership_and_reclaim_guard():
    root = newtree()
    try:
        import socket as _socket
        lock = dedupe.Lock(root)
        # 1) __exit__ must NOT delete a lock that was taken over by another run.
        with lock:
            with open(lock.path, "w") as f:
                f.write(f"{os.getpid() + 1} {int(time.time())} "
                        f"{_socket.gethostname()}\n")
        assert os.path.exists(lock.path), "__exit__ deleted a lock it no longer owned"
        os.remove(lock.path)

        # 2) A fresh .reclaim guard blocks stale-lock reclaim (exit 3).
        with open(lock.path, "w") as f:
            f.write("999999 0\n")                    # dead owner -> stale
        with open(lock.path + ".reclaim", "w") as f:
            f.write("")
        try:
            dedupe.Lock(root).__enter__()
            assert False, "expected exit 3 while another run holds the reclaim guard"
        except SystemExit as e:
            assert e.code == 3
        os.remove(lock.path + ".reclaim")

        # 3) With no guard, the stale lock is reclaimed and acquired.
        l2 = dedupe.Lock(root)
        with l2:
            assert l2.held
            with open(l2.path) as f:
                assert int(f.read().split()[0]) == os.getpid()
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_lock_foreign_host_uses_mtime_only():
    root = newtree()
    try:
        lock = dedupe.Lock(root)
        # A lock synced from another machine: its (alive-here) PID means nothing.
        with open(lock.path, "w") as f:
            f.write(f"{os.getpid()} {int(time.time())} not-this-host\n")
        os.utime(lock.path, None)
        assert lock._is_stale() is False              # fresh mtime -> active
        old = time.time() - (dedupe.STALE_LOCK_SECS + 100)
        os.utime(lock.path, (old, old))
        assert lock._is_stale() is True               # stale mtime -> reclaimable
        os.remove(lock.path)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_lock_ancestor_blocks_subfolder_run():
    root = newtree()
    try:
        import socket as _socket
        sub = os.path.join(root, "sub")
        os.makedirs(sub)
        # a LIVE lock on the parent must block a mutating run on the subfolder
        with open(os.path.join(root, dedupe.LOCK), "w") as f:
            f.write(f"{os.getpid()} {int(time.time())} {_socket.gethostname()}\n")
        try:
            dedupe.Lock(sub).__enter__()
            assert False, "expected exit 3 under a live ancestor lock"
        except SystemExit as e:
            assert e.code == 3
        assert not os.path.exists(os.path.join(sub, dedupe.LOCK)), \
            "sub lock must be released after the ancestor refusal"
        os.remove(os.path.join(root, dedupe.LOCK))
        with dedupe.Lock(sub):          # no ancestor lock -> acquires fine
            pass
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_guardrail_case_variants_refused():
    home = os.path.expanduser("~")
    for variant in (home.lower(), home.upper()):
        if os.path.isdir(variant):      # only on case-insensitive volumes
            p = run("scan", variant)
            assert p.returncode == 3, f"{variant} not refused: {p.stderr}"


def test_hashcache_survives_surrogate_paths():
    """Non-UTF-8 filenames (SMB/exFAT mounts) reach Python as surrogate-escaped
    strings; the cache must degrade to a miss, never crash the scan."""
    root = newtree()
    try:
        hc = dedupe.HashCache(os.path.join(root, "hc.sqlite3"))
        bad = os.path.join(root, "\udcff-weird.bin")
        assert hc.get((bad, 1, 2)) is None       # must not raise
        hc.put((bad, 1, 2), "00" * 32)           # must not raise
        hc.close()
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_undo_survives_corrupt_manifest():
    root = newtree()
    try:
        write(os.path.join(root, "a.bin"), b"same-bytes")
        write(os.path.join(root, "bb.bin"), b"same-bytes")
        run_json("move", root, "--scope", "single")
        manifest = os.path.join(root, "Duplicates", "_manifest.csv")
        with open(manifest, "w") as f:
            f.write("not,a,valid\nmanifest")          # headers gone -> KeyError path
        rc, res = run_json("undo", root)
        assert rc == 0, res                           # contract: no crash, exit 0
        assert os.path.exists(manifest), "corrupt manifest must never be retired"
        assert not os.path.exists(manifest + ".restored")
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_where_true_duplicate_orphan_and_filter():
    root = newtree()
    try:
        write(os.path.join(root, "k.bin"), b"where-content")
        write(os.path.join(root, "longdupe.bin"), b"where-content")
        run_json("move", root, "--scope", "single")
        # quarantined file has its twin outside -> TRUE DUPLICATE, exit 0
        rc, res = run_json("where", root)
        assert rc == 0 and res["ok"] is True, res
        assert res["counts"] == {"ok": 1}, res
        f = res["files"][0]
        assert f["copies_outside"] == ["k.bin"], f
        # name filter + an unknown name gets an explicit answer
        rc, res = run_json("where", root, "longdupe.bin", "nosuch.bin")
        assert res["reported"] == 1, res
        assert res["not_found_in_quarantine"] == ["nosuch.bin"], res
        # kill the keeper -> the quarantined copy is an orphan, exit 2
        os.remove(os.path.join(root, "k.bin"))
        rc, res = run_json("where", root)
        assert rc == 2 and res["ok"] is False, res
        assert res["counts"] == {"orphan": 1}, res
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_resolve_stored_prefers_dedefused():
    """With injective (quote-doubled) manifests the de-defused name is exact;
    probing the raw value first could rename the WRONG file when a defused
    row collides with a genuine quote-named neighbor."""
    root = newtree()
    try:
        dd = os.path.join(root, "Duplicates")
        os.makedirs(dd)
        write(os.path.join(dd, "-x.jpg"), b"defused-target")
        write(os.path.join(dd, "'-x.jpg"), b"genuine-quote-file")
        path, ok = dedupe._resolve_stored(dd, "'-x.jpg")     # defused -x.jpg row
        assert ok and os.path.basename(path) == "-x.jpg"
        path, ok = dedupe._resolve_stored(dd, "''-x.jpg")    # genuine '-x.jpg row
        assert ok and os.path.basename(path) == "'-x.jpg"
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_quarantined_not_orphan_when_original_poisoned():
    """If the only potential original carries a sentinel (poisoned) hash, the
    quarantined file's status is unknowable -> inconclusive, never a CONFIRMED
    orphan (heal must not resurrect a copy next to its real original)."""
    root = newtree()
    try:
        dd = os.path.join(root, "Duplicates")
        write(os.path.join(dd, "_manifest.csv"),
              b"stored_as,original_relpath,keeper_relpath,hash,size,source,scope,when\n")
        write(os.path.join(dd, "q.bin"), b"12345")           # 5 bytes, real hash
        outside = [
            {"hash": "AA", "size": 5,   "rel": "x1.bin", "dir_rel": "."},
            {"hash": "AA", "size": 200, "rel": "x2.bin", "dir_rel": "."},
        ]
        q = dedupe.scan_quarantine(root, "single", _StubScanner(outside), None)
        rec = [r for r in q["records"] if r["name"] == "q.bin"][0]
        assert rec["klass"] == "inconclusive", rec
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_verify_fails_on_unreadable_quarantined_file():
    root = newtree()
    try:
        write(os.path.join(root, "a.bin"), b"same-bytes")
        write(os.path.join(root, "bb.bin"), b"same-bytes")
        run_json("move", root, "--scope", "single")
        dup = os.path.join(root, "Duplicates")
        moved = [n for n in os.listdir(dup)
                 if not n.startswith("_") and not n.startswith(".")]
        os.chmod(os.path.join(dup, moved[0]), 0o000)
        rc, res = run_json("verify", root)
        assert rc == 2, (rc, res)      # unchecked must NEVER read as clean
    finally:
        import shutil
        for dirpath, dirnames, filenames in os.walk(root):
            for n in filenames:
                try:
                    os.chmod(os.path.join(dirpath, n), 0o644)
                except OSError:
                    pass
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------- plain runner
def _main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {e.__class__.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
