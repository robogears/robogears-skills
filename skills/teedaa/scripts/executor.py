#!/usr/bin/env python3
"""teedaa executor — the ONLY code in this skill that moves anything.

Hard properties (rule zero, enforced here, not by discipline):
  * Executes ONLY a plan file whose plan_id is echoed back via --approve.
  * The only primitive is same-volume os.rename inside the scanned root.
    No delete exists. No copy exists (a copy READS bytes — it would download
    placeholders — so cross-device moves are refused, never copied).
  * Every op re-checks reality first: src must exist and match the size the
    plan recorded; anything changed since planning is SKIPPED, not forced.
  * A durable manifest (JSONL, fsynced) precedes success reporting; undo.sh
    plus an engine `undo` restore everything; `verify` proves the state.
  * Single-writer lock at the root; a live run is never stolen.
"""
import json, os, shlex, socket, sqlite3, sys, time

LOCK_NAME = ".teedaa.lock"
STALE_LOCK_SECS = 3600
MANIFEST_DIRNAME = "_teedaa-manifests"


def _cache_root():
    return os.environ.get("TEEDAA_CACHE_DIR") or \
        os.path.expanduser("~/.cache/teedaa")


def _cache_manifest_dir(plan_id):
    d = os.path.join(_cache_root(), "manifests")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, plan_id + ".jsonl")


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class Lock:
    """Single-writer lock (PID+host recorded; a live owner is never
    reclaimed; unknown/foreign owner decided by mtime staleness)."""

    def __init__(self, root):
        self.path = os.path.join(root, LOCK_NAME)
        self.held = False

    def _stale(self):
        pid = None
        host = None
        try:
            with open(self.path) as f:
                parts = f.read().split()
            pid = int(parts[0])
            host = parts[2] if len(parts) > 2 else None
        except (OSError, ValueError, IndexError):
            pid = None
        if host is not None and host != socket.gethostname():
            pid = None
        if pid is not None:
            return not _pid_alive(pid)
        try:
            return time.time() - os.path.getmtime(self.path) > STALE_LOCK_SECS
        except OSError:
            return False

    def __enter__(self):
        for _ in (1, 2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                             0o644)
                os.write(fd, f"{os.getpid()} {int(time.time())} "
                             f"{socket.gethostname()}\n".encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                pass
            except OSError as e:
                raise SystemExit(
                    f"REFUSED: cannot create lock ({e.__class__.__name__})")
            if not self._stale():
                raise SystemExit(
                    "REFUSED: another teedaa run is active on this folder")
            # Reclaim without a TOCTOU race: atomically rename the stale lock
            # to a private name (only one racer wins the rename of that exact
            # inode) rather than a blind remove that could delete a lock a
            # faster racer just created. (audit DAT-aa4eb1e2)
            claimed = self.path + f".stale-{os.getpid()}"
            try:
                os.rename(self.path, claimed)
                os.remove(claimed)
            except OSError:
                pass      # another racer got there first — retry O_CREAT|EXCL
        raise SystemExit("REFUSED: could not acquire the lock")

    def __exit__(self, *a):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.held = False

    def touch(self):
        if self.held:
            try:
                os.utime(self.path, None)
            except OSError:
                pass


def load_plan(path, root, approve):
    """Validate a plan file against the target root and the approval token.
    Returns (plan, error): exactly one is None."""
    try:
        with open(path) as f:
            plan = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"cannot read plan file ({e.__class__.__name__})"
    import planner
    for key in ("plan_id", "root", "operations", "ops_sha256"):
        if key not in plan:
            return None, f"plan file is missing '{key}'"
    if approve != plan["plan_id"]:
        return None, ("approval token does not match — pass exactly: "
                      f"--approve {plan['plan_id']}")
    if os.path.realpath(plan["root"]) != os.path.realpath(root):
        return None, "plan was made for a different folder"
    if planner.ops_checksum(plan["operations"]) != plan["ops_sha256"]:
        return None, ("plan file has been modified since it was generated — "
                      "re-run `plan` and approve a fresh one")
    rr = os.path.realpath(root)
    for op in plan["operations"]:
        if op.get("op") != "rename":
            return None, "plan contains a non-rename operation — refused"
        for side in ("src", "dst"):
            raw = op[side]
            p = os.path.normpath(raw)
            # Lexical containment: reject '..' collapse and non-normalized
            # paths outright — the planner never emits either.
            if p != raw or ".." in p.split(os.sep):
                return None, (f"operation {side} is not a normalized in-tree "
                              "path — refused")
            if not (p == rr or p.startswith(rr + os.sep)):
                return None, f"operation {side} escapes the folder — refused"
            # Symlink containment (normpath does NOT resolve symlinks): the
            # PARENT directory, fully resolved, must still be inside the root.
            # The final component is what rename acts on and must not be
            # resolved (we may be quarantining a symlink itself). This closes
            # the in-tree-symlink escape (audit FS-38603300 / FS-bf4c8253).
            parent_real = os.path.realpath(os.path.dirname(p))
            if not (parent_real == rr or parent_real.startswith(rr + os.sep)):
                return None, (f"operation {side} resolves outside the folder "
                              "through a symlink — refused")
        if os.path.normpath(op["src"]) == rr:
            return None, "operation would move the root itself — refused"
    return plan, None


def _collide(dst):
    base, ext = os.path.splitext(dst)
    i = 1
    while os.path.lexists(dst):
        dst = f"{base} [t{i}]{ext}"
        i += 1
    return dst


def manifest_dir(root, plan_id):
    return os.path.join(root, MANIFEST_DIRNAME, plan_id)


def apply_plan(plan, root, progress=lambda s: None):
    """Execute. Returns stats dict. Manifest is written as we go and fsynced
    every 200 ops and at the end."""
    mdir = manifest_dir(root, plan["plan_id"])
    os.makedirs(mdir, exist_ok=True)
    mpath = os.path.join(mdir, "manifest.jsonl")
    cache_mpath = _cache_manifest_dir(plan["plan_id"])
    stats = {"moved": 0, "skipped_missing": 0, "skipped_changed": 0,
             "failed": 0, "collisions": 0, "manifest": mpath,
             "manifest_mirror": cache_mpath}
    rows = []
    with Lock(root) as lock, open(mpath, "a") as mf, \
            open(cache_mpath, "a") as cf:
        for i, op in enumerate(plan["operations"]):
            src, dst = op["src"], op["dst"]
            try:
                st = os.lstat(src)
            except OSError:
                stats["skipped_missing"] += 1
                continue
            want = op.get("size")
            if want is not None and not os.path.isdir(src) and \
                    st.st_size != want:
                stats["skipped_changed"] += 1     # changed since planning
                continue
            final_dst = _collide(dst)
            if final_dst != dst:
                stats["collisions"] += 1
            # Write-ahead: record the INTENDED move and fsync it BEFORE the
            # rename, so a hard crash can never leave a moved file with no
            # manifest row (that would make verify falsely vouch all-clear).
            # A row whose rename never happened lands in undo's benign
            # already_undone/already_home branch — harmless. (audit DAT-*)
            intent = {"src": src, "dst": final_dst, "size": st.st_size,
                      "status": "moved", "plan_id": plan["plan_id"],
                      "ts": int(time.time())}
            line = json.dumps(intent, ensure_ascii=False)
            mf.write(line + "\n")
            mf.flush()
            os.fsync(mf.fileno())
            cf.write(line + "\n")
            cf.flush()
            os.fsync(cf.fileno())
            try:
                os.makedirs(os.path.dirname(final_dst), exist_ok=True)
                os.rename(src, final_dst)
            except OSError as e:
                # The rename did NOT happen. Append a compensating 'failed'
                # row (also fsynced) so the durable manifest records that this
                # dst never received a file — verify/undo reconcile the earlier
                # write-ahead 'moved' row against it and ignore both. Covers
                # the case where src vanished mid-window (ENOENT): without the
                # compensation the orphan 'moved' row would false-alarm verify.
                fail = {"src": src, "dst": final_dst, "status": "failed",
                        "errno": e.errno, "ts": int(time.time())}
                fl = json.dumps(fail, ensure_ascii=False)
                mf.write(fl + "\n")
                mf.flush()
                os.fsync(mf.fileno())
                cf.write(fl + "\n")
                stats["failed"] += 1
                rows.append(fail)
                continue
            rows.append(intent)
            stats["moved"] += 1
            if i % 200 == 199:
                lock.touch()
                progress(f"{stats['moved']:,} moved")
        cf.flush()
        os.fsync(cf.fileno())
    # undo.sh is generated from the DURABLE manifest on disk (not just this
    # run's in-memory rows) so a re-run after a crash still restores the
    # first run's moves. (audit DAT-fc6cf937)
    _write_undo_script(mdir, read_manifest(mpath))
    _write_readme(mdir, plan, stats)
    return stats


def _write_undo_script(mdir, rows):
    path = os.path.join(mdir, "undo.sh")
    failed_dsts = {os.path.realpath(row["dst"]) for row in rows
                   if row.get("status") == "failed" and row.get("dst")}
    with open(path, "w") as f:
        f.write("#!/bin/sh\n# teedaa undo — restores every moved file to its "
                "original location.\n# Safe to re-run; existing originals are "
                "kept as *.restored.\nset -u\n")
        for row in reversed(rows):
            if row.get("status") != "moved":
                continue
            if os.path.realpath(row["dst"]) in failed_dsts:
                continue                       # void write-ahead row
            src, dst = row["src"], row["dst"]
            qs, qd = shlex.quote(src), shlex.quote(dst)
            f.write(f'if [ -e {qd} ]; then mkdir -p {shlex.quote(os.path.dirname(src))}; '
                    f'if [ -e {qs} ]; then mv {qd} {qs}.restored; '
                    f'else mv {qd} {qs}; fi; fi\n')
        f.write('echo "undo complete"\n')
    os.chmod(path, 0o755)


def _write_readme(mdir, plan, stats):
    with open(os.path.join(mdir, "README.txt"), "w") as f:
        f.write(
            "teedaa moved these files — nothing was deleted.\n\n"
            f"Plan: {plan['plan_id']} ({plan['type']})\n"
            f"Moved: {stats['moved']} items\n"
            f"When: {time.strftime('%Y-%m-%d %H:%M')}\n\n"
            "manifest.jsonl lists every move (src -> dst).\n"
            "To put EVERYTHING back: run ./undo.sh, or:\n"
            f"  python3 <teedaa>/scripts/teedaa.py undo \"{plan['root']}\" "
            f"--plan-id {plan['plan_id']}\n\n"
            "Review window: look through the moved files for "
            f"{plan.get('review_after_days', 30)} days. If nothing is missed, "
            "YOU may delete the quarantine folder yourself — teedaa never "
            "deletes anything.\n")


def iter_manifests(root, plan_id=None):
    """Yield (plan_id, manifest_path) for manifests BELONGING TO root:
    in-tree first, then cache mirrors — but a mirror is only accepted when
    its rows actually live under this root (mirrors from other folders and
    long-gone test trees must never fail this folder's verify)."""
    seen = set()
    rr = os.path.realpath(root)
    mroot = os.path.join(root, MANIFEST_DIRNAME)
    if os.path.isdir(mroot):
        # newest-first (reverse chronological) so undo replays recent plans
        # before older ones, per the undo contract. (audit FS-de8a6f23 sib)
        entries = []
        for pid in os.listdir(mroot):
            if plan_id and pid != plan_id:
                continue
            mp = os.path.join(mroot, pid, "manifest.jsonl")
            if os.path.exists(mp):
                try:
                    mt = os.path.getmtime(mp)
                except OSError:
                    mt = 0
                entries.append((mt, pid, mp))
        for _mt, pid, mp in sorted(entries, reverse=True):
            seen.add(pid)
            yield pid, mp
    cdir = os.path.join(_cache_root(), "manifests")
    if os.path.isdir(cdir):
        for fn in sorted(os.listdir(cdir)):
            if not fn.endswith(".jsonl"):
                continue
            pid = fn[:-6]
            if (plan_id and pid != plan_id) or pid in seen:
                continue
            mp = os.path.join(cdir, fn)
            rows = read_manifest(mp)
            first = next((r for r in rows if r.get("src")), None)
            if first is None or not os.path.realpath(
                    first["src"]).startswith(rr + os.sep):
                continue
            yield pid, mp


def read_manifest(path):
    rows = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    rows.append({"status": "corrupt-line"})
    except OSError:
        pass
    return rows


def _quarantine_files(root, pid):
    """Every real file under this plan's quarantine dir (relative to root)."""
    qroot = os.path.join(root, "_Quarantine", pid)
    found = set()
    for dirpath, _dirs, files in os.walk(qroot):
        for fn in files:
            found.add(os.path.join(dirpath, fn))
    return found


def verify_plans(root, plan_id=None):
    """Read-only proof: every manifest row's dst exists and src is gone, AND
    every file sitting in the quarantine is accounted for by a manifest row.
    Problems: dst missing (data unaccounted!), corrupt rows, or an
    'unmanifested' quarantined file (present but with no way back via undo)."""
    result = {"plans": [], "ok": True}
    for pid, mp in iter_manifests(root, plan_id):
        rows = read_manifest(mp)
        r = {"plan_id": pid, "rows": len(rows), "moved_ok": 0,
             "already_undone": 0, "dst_missing": 0, "corrupt": 0,
             "unmanifested": 0}
        # a dst with a 'failed' row never received a file — its earlier
        # write-ahead 'moved' row is void (reconcile them)
        failed_dsts = {os.path.realpath(row["dst"]) for row in rows
                       if row.get("status") == "failed" and row.get("dst")}
        manifest_dsts = set()
        for row in rows:
            if row.get("status") == "corrupt-line":
                r["corrupt"] += 1
                continue
            if row.get("status") == "failed":
                continue
            if os.path.realpath(row["dst"]) in failed_dsts:
                continue                       # void write-ahead row
            manifest_dsts.add(os.path.realpath(row["dst"]))
            dst_there = os.path.lexists(row["dst"])
            src_there = os.path.lexists(row["src"])
            if dst_there:
                r["moved_ok"] += 1
            elif src_there:
                r["already_undone"] += 1
            else:
                r["dst_missing"] += 1
        # orphan detection: a quarantined file with no manifest row cannot be
        # restored by undo — the fingerprint of a crash-window move or a
        # foreign process dropping files into _Quarantine. (QoL hardening)
        for qf in _quarantine_files(root, pid):
            if os.path.realpath(qf) not in manifest_dsts:
                r["unmanifested"] += 1
        if r["dst_missing"] or r["corrupt"] or r["unmanifested"]:
            result["ok"] = False
        result["plans"].append(r)
    return result


def undo_plans(root, plan_id=None, progress=lambda s: None):
    """Reverse-chronological restore of every manifest row. Never deletes;
    collisions at the original location restore as <name>.restored."""
    rr = os.path.realpath(root)
    result = {"plans": [], "restored": 0, "leftover": 0}
    with Lock(root):
        for pid, mp in iter_manifests(root, plan_id):
            rows = read_manifest(mp)
            r = {"plan_id": pid, "restored": 0, "already_home": 0,
                 "missing": 0, "as_restored": 0, "failed": 0, "corrupt": 0,
                 "escaping": 0}
            failed_dsts = {os.path.realpath(row["dst"]) for row in rows
                           if row.get("status") == "failed" and row.get("dst")}
            for row in reversed(rows):
                if row.get("status") == "corrupt-line":
                    r["corrupt"] += 1
                    continue
                if row.get("status") != "moved":
                    continue
                src, dst = row["src"], row["dst"]
                if os.path.realpath(dst) in failed_dsts:
                    continue                   # void write-ahead row
                # Containment: a manifest row (in-tree file, could be tampered
                # or synced from elsewhere) must not make undo write outside
                # the folder. Resolve the PARENTS, keep the final component.
                sp = os.path.realpath(os.path.dirname(os.path.normpath(src)))
                dp = os.path.realpath(os.path.dirname(os.path.normpath(dst)))
                if not all((q == rr or q.startswith(rr + os.sep))
                           for q in (sp, dp)):
                    r["escaping"] += 1
                    continue
                if not os.path.lexists(dst):
                    if os.path.lexists(src):
                        r["already_home"] += 1
                    else:
                        r["missing"] += 1
                    continue
                target = src
                if os.path.lexists(src):
                    target = src + ".restored"
                    r["as_restored"] += 1
                try:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    os.rename(dst, target)
                    r["restored"] += 1
                except OSError:
                    r["failed"] += 1
            # Retire the manifest only when EVERYTHING is accounted for —
            # corrupt or escaping rows (the artifacts of a power-loss crash or
            # a tampered manifest) keep it around for investigation.
            if not (r["failed"] or r["missing"] or r["corrupt"]
                    or r["escaping"]):
                try:
                    os.rename(mp, mp + ".undone-" +
                              time.strftime("%Y%m%d%H%M%S"))
                except OSError:
                    pass
            else:
                result["leftover"] += (r["failed"] + r["missing"]
                                       + r["corrupt"] + r["escaping"])
            result["restored"] += r["restored"]
            result["plans"].append(r)
            progress(f"{pid}: {r['restored']} restored")
    return result
