#!/usr/bin/env python3
"""teedaa planner — turns the classified index into APPROVAL-GATED plan files.

A plan is a JSON document proposing rename-only operations. Generating a plan
touches NOTHING. Executing one is `apply`'s job, and apply demands the plan's
own id back as an explicit --approve token — so nothing can run by accident.

Plan types (v0.1):
  junk-safe            tier-A junk -> _Quarantine/<plan-id>/...   (🟢)
  junk-review          tier-B junk -> _Quarantine/<plan-id>/...   (🟡, opt-in)
  dormant-caches       rebuildable dirs of projects untouched >18mo (🟡)
  photo-consolidation  CERT+LIKELY captures -> Photos/YYYY/MM      (🟡)
  screenshots          screenshots -> Screenshots/YYYY             (🟡)
Tier-C ("look first") items are deliberately NOT planned — report-only.
Sensitive files can never appear here: the classifier's precedence removed
them before any junk/photo class was assigned.

Consent friction ladder (embedded in each plan for the skill to enforce):
  simple    < 1,000 items and < 5 GB   -> plain confirmation
  explicit  >= that                    -> confirmation naming counts+sizes
  typed     > 10,000 items or > 50 GB  -> user must type the folder name
"""
import hashlib, json, os, re, time, uuid
from collections import defaultdict


def ops_checksum(operations):
    """Integrity stamp over the operations array. Guards against a plan file
    getting corrupted/hand-mangled between `plan` and `apply` (accident
    protection, not cryptography — apply refuses on mismatch)."""
    blob = json.dumps(operations, sort_keys=True,
                      separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()

SIDECAR_EXTS = {".aae", ".xmp", ".thm", ".lrv"}
DORMANT_SECS = 540 * 86400            # ~18 months (rebuildable dep dirs)
DORMANT_ARCHIVE_SECS = 3 * 365 * 86400  # 3 years (cold-archive threshold)

DATE_RES = [
    re.compile(r"^(?:IMG|VID|PXL)[-_](\d{4})(\d{2})(\d{2})[-_]"),
    re.compile(r"^(\d{4})-(\d{2})-(\d{2}) \d{2}\.\d{2}\.\d{2}"),
    re.compile(r"^(\d{4})(\d{2})(\d{2})_\d{6}"),
    re.compile(r"^(?:IMG|VID|AUD|PTT)-(\d{4})(\d{2})(\d{2})-WA"),
    re.compile(r"^Screen ?Shot (\d{4})-(\d{2})-(\d{2})", re.I),
    re.compile(r"^Screenshot[_ ](\d{4})-?(\d{2})-?(\d{2})", re.I),
]
GARBAGE_YEARS = {1904, 1970, 1980}


def capture_date(name, mtime):
    """(year, month, source) — filename pattern wins; mtime is the fallback;
    (0, 0, 'unknown') when neither is trustworthy."""
    for rx in DATE_RES:
        m = rx.match(name)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
            if 1990 <= y <= time.gmtime().tm_year + 1 and 1 <= mo <= 12:
                return y, mo, "filename"
    if mtime and mtime > 0:
        t = time.localtime(mtime)   # LOCAL time: UTC bucketing mis-folders
        # bound the mtime year like the filename branch, so a corrupt future
        # timestamp routes to _Unknown-Date instead of Photos/2099/. (audit)
        if (t.tm_year not in GARBAGE_YEARS and   # late-night photos (UAE +4)
                1990 <= t.tm_year <= time.gmtime().tm_year + 1):
            return t.tm_year, t.tm_mon, "mtime"
    return 0, 0, "unknown"


def consent_level(count, total_bytes):
    if count > 10000 or total_bytes > 50 * (1 << 30):
        return "typed"
    if count >= 1000 or total_bytes >= 5 * (1 << 30):
        return "explicit"
    return "simple"


def _dir_paths(con, root):
    nodes = {}
    for did, name, parent in con.execute("SELECT id, name, parent FROM dirs"):
        nodes[did] = (name, parent)
    paths = {}

    def resolve(did):
        chain = []
        cur = did
        while cur is not None and cur not in paths:
            chain.append(cur)
            nm, par = nodes[cur]
            if par is None:
                paths[cur] = root
                break
            cur = par
        for node in reversed(chain):
            if node in paths:
                continue
            nm, par = nodes[node]
            paths[node] = os.path.join(paths[par], nm)
        return paths[did]
    for did in nodes:
        resolve(did)
    return paths


def _plan_shell(root, ptype, tier, evidence):
    pid = f"{ptype}-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    return {
        "plan_id": pid, "type": ptype, "tier": tier,
        "created_at": int(time.time()), "root": root,
        "engine_version": None,          # filled by cmd_plan
        "evidence": evidence,
        "operations": [], "warnings": [],
        "review_after_days": 30,
        "consent": None,                 # filled at finalize
    }


def _finalize(plan):
    n = len(plan["operations"])
    b = sum(op["size"] or 0 for op in plan["operations"])
    plan["op_count"] = n
    plan["total_bytes"] = b
    plan["consent"] = consent_level(n, b)
    plan["ops_sha256"] = ops_checksum(plan["operations"])
    if n > 2000:
        plan["warnings"].append(
            f"sync-churn: {n:,} renames will make Dropbox/OneDrive re-index; "
            "expect the sync icon to spin for a while")
    return plan if n else None


def _qdst(root, pid, src):
    rel = os.path.relpath(src, root)
    return os.path.join(root, "_Quarantine", pid, rel)


def build_plans(con, root, engine_version="0.1.0"):
    """Return a list of finalized plan dicts (possibly empty). Read-only."""
    paths = _dir_paths(con, root)
    plans = []

    rows = list(con.execute(
        "SELECT f.rowid, f.parent, f.name, f.size, f.mtime, f.cls, "
        "f.link_target, f.dataless, d.zone, d.prot "
        "FROM files f JOIN dirs d ON d.id = f.parent WHERE f.cls IS NOT NULL"))

    # sibling map for sidecar atomicity (photo moves carry their sidecars)
    stem_map = defaultdict(list)
    for r in rows:
        name = r[2]
        i = name.lower().rfind(".")
        stem = name[:i] if i > 0 else name
        stem_map[(r[1], stem.lower())].append(r)

    symlink_targets = set()
    for parent, lt in con.execute("SELECT parent, link_target FROM files "
                                  "WHERE link_target IS NOT NULL"):
        if lt:
            base = paths.get(parent, "")
            symlink_targets.add(
                lt if os.path.isabs(lt)
                else os.path.normpath(os.path.join(base, lt)))

    def op(plan, src, dst, size, evidence):
        if src in symlink_targets:
            plan["warnings"].append(
                f"a shortcut/symlink elsewhere points at {src} — moving it "
                "will break that link")
        plan["operations"].append({"op": "rename", "src": src, "dst": dst,
                                   "size": size, "evidence": evidence})

    # ---- junk quarantines (A and B kept as separate consents) --------------
    for tier, ptype, badge in (("A", "junk-safe", "🟢"),
                               ("B", "junk-review", "🟡")):
        plan = _plan_shell(root, ptype, badge,
                           "certain junk patterns" if tier == "A"
                           else "probable junk — glance at the list first")
        fam_counts = defaultdict(int)
        for rowid, parent, name, size, mtime, cls, lt, dl, zone, prot in rows:
            if not cls.startswith("j" + tier):
                continue
            src = os.path.join(paths[parent], name)
            fam = cls.split(":")[1]
            fam_counts[fam] += 1
            op(plan, src, _qdst(root, plan["plan_id"], src), size or 0,
               f"{fam}: {cls}")
        plan["evidence"] += " — " + ", ".join(
            f"{k}×{v}" for k, v in sorted(fam_counts.items())) if fam_counts \
            else ""
        p = _finalize(plan)
        if p:
            plans.append(p)

    # ---- dormant caches ----------------------------------------------------
    plan = _plan_shell(root, "dormant-caches", "🟡",
                       "rebuildable dirs whose owning project has not been "
                       "touched in 18+ months (regenerate on demand)")
    now = time.time()
    cache_dirs = list(con.execute(
        "SELECT id, parent, zone FROM dirs WHERE zone IN "
        "('cache','rebuildable') AND prot IS NOT NULL"))
    for did, parent, zone in cache_dirs:
        if parent is None:
            continue
        # dormancy = newest file mtime in the PARENT project subtree,
        # excluding the cache dir itself
        newest = con.execute(
            "SELECT MAX(f.mtime) FROM files f JOIN dirs d ON d.id=f.parent "
            "WHERE (d.prot=(SELECT prot FROM dirs WHERE id=?) OR d.id=?) "
            "AND f.parent NOT IN (WITH RECURSIVE sub(i) AS "
            "(SELECT ? UNION SELECT id FROM dirs, sub WHERE parent=sub.i) "
            "SELECT i FROM sub)",
            (did, parent, did)).fetchone()[0]
        if newest is None or now - newest < DORMANT_SECS:
            continue
        src = paths[did]
        size = con.execute(
            "WITH RECURSIVE sub(i) AS (SELECT ? UNION SELECT id FROM dirs, "
            "sub WHERE parent=sub.i) SELECT COALESCE(SUM(size),0) FROM files "
            "WHERE parent IN (SELECT i FROM sub) AND hl_first=1",
            (did,)).fetchone()[0]
        op(plan, src, _qdst(root, plan["plan_id"], src), size,
           f"{zone} dir; project idle since "
           f"{time.strftime('%Y-%m', time.localtime(newest))}")
    p = _finalize(plan)
    if p:
        plans.append(p)

    # ---- photo consolidation ----------------------------------------------
    plan = _plan_shell(root, "photo-consolidation", "🟡",
                       "certain/likely personal captures scattered outside a "
                       "photo tree — proposed home: Photos/YYYY/MM (original "
                       "filenames kept)")
    photos_root = os.path.join(root, "Photos")
    seen_rowids = set()
    for rowid, parent, name, size, mtime, cls, lt, dl, zone, prot in rows:
        if cls not in ("ph:cert", "ph:likely") or rowid in seen_rowids:
            continue
        dpath = paths[parent]
        if dpath.startswith(photos_root + os.sep) or dpath == photos_root:
            continue        # already home
        low = dpath.lower()
        if "/camera uploads" in low or "/dcim" in low:
            pass            # these are sources worth consolidating too
        y, mo, dsrc = capture_date(name, mtime)
        if y:
            dst_dir = os.path.join(photos_root, f"{y:04d}", f"{mo:02d}")
        else:
            dst_dir = os.path.join(photos_root, "_Unknown-Date",
                                   os.path.basename(dpath) or "root")
        src = os.path.join(dpath, name)
        group = []
        i = name.lower().rfind(".")
        stem = name[:i].lower() if i > 0 else name.lower()
        for sib in stem_map.get((parent, stem), []):
            sname = sib[2]
            si = sname.lower().rfind(".")
            sext = sname.lower()[si:] if si > 0 else ""
            if sib[0] != rowid and (sext in SIDECAR_EXTS or
                                    sib[5] in ("ph:side", "ph:cert",
                                               "ph:likely")):
                group.append(sib)
        op(plan, src, os.path.join(dst_dir, name), size or 0,
           f"{cls} · date from {dsrc}")
        seen_rowids.add(rowid)
        for sib in group:
            if sib[0] in seen_rowids:
                continue
            seen_rowids.add(sib[0])
            ssrc = os.path.join(dpath, sib[2])
            op(plan, ssrc, os.path.join(dst_dir, sib[2]), sib[3] or 0,
               "travels with its photo (sidecar/pair)")
    if any(o["dst"].startswith(os.path.join(photos_root, "_Unknown-Date"))
           for o in plan["operations"]):
        plan["warnings"].append(
            "some photos had no trustworthy date — they go to "
            "Photos/_Unknown-Date/<source-folder>/ for human dating, "
            "never guessed")
    p = _finalize(plan)
    if p:
        plans.append(p)

    # ---- cold-archive (PARA Archives) --------------------------------------
    # Top-level items whose entire subtree has been untouched for 3+ years ->
    # Archive/<year-of-last-activity>/<item>. A whole-item single rename;
    # NOTHING is judged junk here (a decade-old item is precisely what an
    # archive is for). Skips protected zones and Archive/Photos/Screenshots.
    plan = _plan_shell(root, "cold-archive", "🟡",
                       "top-level items with no activity in 3+ years -> "
                       "Archive/<year>/ (nothing here is called junk — this is "
                       "just tidying cold storage away from the working set)")
    root_id_row = con.execute(
        "SELECT id FROM dirs WHERE parent IS NULL").fetchone()
    if root_id_row:
        root_id = root_id_row[0]
        skip_names = {"Archive", "Photos", "Screenshots", "_Quarantine",
                      "_teedaa-manifests"}
        # top-level DIRs (skip protected zones and reserved names)
        for did, dname, zone, prot in con.execute(
                "SELECT id, name, zone, prot FROM dirs WHERE parent=?",
                (root_id,)):
            if dname in skip_names or prot is not None:
                continue
            # Rule zero: never move a folder that holds ANY sensitive file —
            # archiving the whole item would sweep sensitive data into a plan.
            has_sensitive = con.execute(
                "WITH RECURSIVE sub(i) AS (SELECT ? UNION SELECT id FROM dirs,"
                " sub WHERE parent=sub.i) SELECT 1 FROM files WHERE parent IN "
                "(SELECT i FROM sub) AND cls LIKE 'sn:%' LIMIT 1",
                (did,)).fetchone()
            if has_sensitive:
                plan["warnings"].append(
                    f"'{dname}' left in place — it contains sensitive-looking "
                    "files, which teedaa never moves")
                continue
            newest = con.execute(
                "WITH RECURSIVE sub(i) AS (SELECT ? UNION SELECT id FROM dirs,"
                " sub WHERE parent=sub.i) SELECT MAX(mtime) FROM files "
                "WHERE parent IN (SELECT i FROM sub)", (did,)).fetchone()[0]
            if newest is None or now - newest < DORMANT_ARCHIVE_SECS:
                continue
            y = time.localtime(newest).tm_year
            if not (1990 <= y <= time.gmtime().tm_year):
                continue
            size = con.execute(
                "WITH RECURSIVE sub(i) AS (SELECT ? UNION SELECT id FROM dirs,"
                " sub WHERE parent=sub.i) SELECT COALESCE(SUM(size),0) FROM "
                "files WHERE parent IN (SELECT i FROM sub) AND hl_first=1",
                (did,)).fetchone()[0]
            src = os.path.join(root, dname)
            op(plan, src, os.path.join(root, "Archive", f"{y:04d}", dname),
               size, f"whole folder idle since {y}")
        # top-level FILES (never a sensitive one — rule zero)
        for name, size, mtime, fcls in con.execute(
                "SELECT name, size, mtime, cls FROM files WHERE parent=?",
                (root_id,)):
            if name is None or mtime is None or \
                    now - mtime < DORMANT_ARCHIVE_SECS:
                continue
            if fcls and fcls.startswith("sn:"):
                continue
            y = time.localtime(mtime).tm_year
            if not (1990 <= y <= time.gmtime().tm_year):
                continue
            src = os.path.join(root, name)
            op(plan, src, os.path.join(root, "Archive", f"{y:04d}", name),
               size or 0, f"idle since {y}")
    p = _finalize(plan)
    if p:
        plans.append(p)

    # ---- screenshots -------------------------------------------------------
    plan = _plan_shell(root, "screenshots", "🟡",
                       "screenshots -> Screenshots/YYYY (they bury real "
                       "photos and age badly)")
    for rowid, parent, name, size, mtime, cls, lt, dl, zone, prot in rows:
        if cls != "ph:screen":
            continue
        y, _mo, _src = capture_date(name, mtime)
        dst_dir = os.path.join(root, "Screenshots", f"{y:04d}" if y else "_Undated")
        op(plan, os.path.join(paths[parent], name),
           os.path.join(dst_dir, name), size or 0, "screenshot pattern")
    p = _finalize(plan)
    if p:
        plans.append(p)

    for p in plans:
        p["engine_version"] = engine_version
        if root.startswith(os.path.expanduser("~/Library/CloudStorage")):
            p["warnings"].append(
                "Dropbox shared-folder detection is not implemented in v0.1: "
                "if any of these paths live inside a folder SHARED with other "
                "people, a move removes it for every member — check before "
                "approving")
    return plans
