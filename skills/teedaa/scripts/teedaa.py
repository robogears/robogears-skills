#!/usr/bin/env python3
"""teedaa engine — strictly read-only folder audit with a permission-gated,
move-only cleanup planner.

RULE ZERO (non-negotiable, enforced in code):
  * `scan`, `report`, and `plan` NEVER modify the target tree.
  * `apply` executes ONLY an explicitly approved plan file, and the only
    mutation it knows is a same-volume rename into a quarantine folder with a
    manifest and an undo script. Nothing is ever deleted, ever.
  * Cloud placeholders (Dropbox / OneDrive dataless files) are NEVER read.
    The engine arms an OS-level kill switch (setiopolicy_np: dataless
    materialization OFF) so a download cannot happen even by accident — any
    would-download operation fails with EDEADLK instead of fetching bytes.

Subcommands:
  scan   <folder>     index metadata into SQLite (resumable; read-only)
  report <folder>     markdown report + dashboard HTML from the index
  plan   <folder>     risk-tiered cleanup proposals as plan JSON (read-only)
  apply  <folder> --plan FILE   execute ONE approved plan (rename-only)
  verify <folder>     prove quarantine reversibility (read-only)
  undo   <folder>     restore everything from a quarantine manifest

Common flags: --json (machine output only), --quiet (suppress progress).
Exit codes: 0 = OK; 2 = verification found real problems; 3 = refused target /
lock contested / safety cannot be guaranteed; 64 = command-line usage error.
"""
import argparse, ctypes, errno, hashlib, json, os, re, shutil, signal, sqlite3, stat as statmod, sys, tempfile, time, unicodedata
from collections import defaultdict
from urllib.request import pathname2url

__version__ = "0.1.0"
SCHEMA_VERSION = "3"

IS_MACOS = sys.platform == "darwin"

# macOS st_flags bits (see chflags(2) / TN3150)
UF_COMPRESSED = 0x20          # decmpfs-compressed but fully local
SF_DATALESS = 0x40000000      # File Provider placeholder: bytes are NOT here
SF_FIRMLINK = 0x00800000      # firmlink (system volume seam)

# setiopolicy_np(2) constants — the dataless-materialization kill switch
IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES = 3
IOPOL_SCOPE_PROCESS = 0
IOPOL_MATERIALIZE_DATALESS_FILES_OFF = 1

DEPTH_CAP = 64                # deeper subtrees are recorded, not walked
PROGRESS_EVERY = 1.0          # seconds between progress repaints
COMMIT_ROWS = 20000           # flush batches at this many buffered rows
COMMIT_SECS = 2.0             # ... or this many seconds, whichever first

# mtimes at (or suspiciously near) these epochs are timestamp garbage, not age
GARBAGE_YEARS = {1904, 1970, 1980}

_JSON_ONLY = False


def nfc(s):
    """Normalize to NFC so macOS NFD names match Dropbox's NFC records."""
    return unicodedata.normalize("NFC", s)


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def safe(s):
    """Neutralize control chars in a path before printing, so a hostile
    filename cannot forge log lines or emit ANSI escapes."""
    return _CTRL.sub("?", str(s))


_MD_META = re.compile(r"([`*_\[\]()<>!|#\\])")


def safe_md(s):
    """safe() plus backslash-escaping of markdown metacharacters, so a hostile
    filename in the report can't inject links/HTML/emphasis. (audit)"""
    return _MD_META.sub(r"\\\1", _CTRL.sub("?", str(s)))


def out(msg):
    """Human-facing line. Goes to stderr in --json mode so stdout stays clean."""
    print(msg, file=(sys.stderr if _JSON_ONLY else sys.stdout))


def emit_json(result):
    print("RESULT_JSON: " + json.dumps(result))  # always stdout


def write_atomic(path, text):
    """Write via a same-dir temp + os.replace so a crash mid-write never
    leaves a truncated artifact. (QoL: atomic report/plan writes)"""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}" if u != "B" else f"{int(n)} B"
        n /= 1024.0


def st_is_dataless(st):
    """True when the bytes are NOT on disk and reading would download.
    SF_DATALESS wins over everything: decmpfs-compressed files also report 0
    blocks while being fully local, and File Provider evicts VIA a dataless
    decmpfs type — so st_blocks==0 alone must never be the test (a prior
    engine's would-download regression, caught 2026-07-21)."""
    return bool(getattr(st, "st_flags", 0) & SF_DATALESS)


def arm_killswitch():
    """Turn OFF dataless materialization for this whole process. After this,
    any operation that would download a placeholder raises EDEADLK instead.
    Returns True when armed, False when unavailable (non-macOS) — callers
    refuse CloudStorage targets in that case rather than proceed unsafely."""
    if not IS_MACOS:
        return False
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        rc = libc.setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES,
                                 IOPOL_SCOPE_PROCESS,
                                 IOPOL_MATERIALIZE_DATALESS_FILES_OFF)
        return rc == 0
    except OSError:
        return False


def cloudstorage_root():
    return os.path.expanduser("~/Library/CloudStorage")


# ---------------- Dropbox nucleus DB (metadata mining, zero downloads) -------
# The nucleus path and root detection honor env overrides so the test suite
# can exercise every code path against synthetic fixtures — real Dropbox data
# is never touched by tests (build rule).
def nucleus_path():
    return os.environ.get("TEEDAA_NUCLEUS") or \
        os.path.expanduser("~/.dropbox/instance1/sync_fp/nucleus.sqlite3")


def _dropbox_roots():
    override = os.environ.get("TEEDAA_DBX_ROOT")
    if override:
        return [os.path.realpath(override)]
    roots = []
    cs = cloudstorage_root()
    if os.path.isdir(cs):
        try:
            for n in os.listdir(cs):
                if n.startswith("Dropbox"):
                    roots.append(os.path.realpath(os.path.join(cs, n)))
        except OSError:
            pass
    legacy = os.path.expanduser("~/Dropbox")
    if os.path.isdir(legacy):
        roots.append(os.path.realpath(legacy))
    return roots


def dropbox_root_for(path):
    rp = os.path.realpath(path)
    for root in _dropbox_roots():
        if rp == root or rp.startswith(root + os.sep):
            return root
    return None


# minimal protobuf reader (proven against nucleus since check-for-dupes v0.1.0)
def _rv(b, i):
    r = 0
    s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        if not (x & 0x80):
            return r, i
        s += 7


def _fields(b):
    if not isinstance(b, (bytes, bytearray)):
        return {}
    out_ = {}
    i = 0
    while i < len(b):
        try:
            tag, i = _rv(b, i)
        except (IndexError, TypeError):
            break
        f, w = tag >> 3, tag & 7
        if w == 0:
            try:
                v, i = _rv(b, i)
            except (IndexError, TypeError):
                break
        elif w == 2:
            try:
                ln, i = _rv(b, i)
            except (IndexError, TypeError):
                break
            v = b[i:i + ln]
            i += ln
            if len(v) != ln:
                break
        elif w == 5:
            v = b[i:i + 4]
            i += 4
        elif w == 1:
            v = b[i:i + 8]
            i += 8
        else:
            break
        out_.setdefault(f, []).append(v)
    return out_


def _first_msg(fielddict, key):
    if key not in fielddict:
        return None
    v = fielddict[key][0]
    return v if isinstance(v, (bytes, bytearray)) else None


class DropboxDB:
    """Read-only view of Dropbox's local sync DB (fp_local_tree), snapshotted
    with a hard deadline (an unbounded sqlite backup of Dropbox's hot DB
    livelocks during re-indexing — observed live 2026-07-21).

    Per entry we extract: is-file (field 4 present), logical size (4.2),
    whole-file SHA-256 (4.6.1, 32 bytes — verified equal to sha256(file)),
    original client mtime (field 13; server ts field 3 as fallback). Zero-byte
    files omit 4.2/4.6 entirely (proto3 default) → size 0, no hash."""

    BACKUP_DEADLINE = 45.0

    def __init__(self, workdir, dbx_root):
        # realpath defensively: resolve_dir compares relpath() components, and
        # a symlinked root (macOS /var -> /private/var) would break every
        # lookup with a lexical '..' mismatch.
        self.root = os.path.realpath(dbx_root)
        src = nucleus_path()
        snap = os.path.join(workdir, "nucleus_snapshot.db")
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(snap + suffix)
            except OSError:
                pass
        start = time.monotonic()

        def _deadline(status, remaining, total):
            if time.monotonic() - start > self.BACKUP_DEADLINE:
                raise TimeoutError("nucleus backup exceeded deadline")
        backed_up = False
        try:
            srccon = sqlite3.connect("file:" + pathname2url(src) + "?mode=ro",
                                     uri=True)
            try:
                dstcon = sqlite3.connect(snap)
                try:
                    srccon.backup(dstcon, pages=2048, sleep=0.05,
                                  progress=_deadline)
                    backed_up = True
                finally:
                    dstcon.close()
            finally:
                srccon.close()
        except (sqlite3.Error, TimeoutError, OSError):
            backed_up = False
        if not backed_up:
            out("  (Dropbox's database is busy — using file-copy snapshot instead)")
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(snap + suffix)
                except OSError:
                    pass
            shutil.copy(src, snap)
            for ext in ("-wal", "-shm"):
                try:
                    shutil.copy(src + ext, snap + ext)
                except OSError:
                    pass
        con = sqlite3.connect(snap)
        self.parent = {}                    # itemid -> (dir_itemid, nfc(name))
        self.children = defaultdict(list)   # dir_itemid -> [itemid]
        self.info = {}                      # itemid -> (is_file, size, hash|None, mtime|None)
        self._byname = defaultdict(list)    # nfc(name) -> [itemid]
        hash_sizes = defaultdict(set)       # hash -> {sizes} (poison detection)
        for itemid, dirid, fn, md in con.execute(
                "SELECT itemid, dir_itemid, CAST(filename AS TEXT), metadata "
                "FROM fp_local_tree WHERE latest"):
            if fn is None:
                continue
            fn = nfc(fn)
            self.parent[itemid] = (dirid, fn)
            self.children[dirid].append(itemid)
            self._byname[fn].append(itemid)
            is_file = False
            size = 0
            h = None
            mtime = None
            if md:
                top = _fields(md)
                f4m = _first_msg(top, 4)
                if f4m is not None:
                    is_file = True
                    f4 = _fields(f4m)
                    sv = f4.get(2, [0])[0]
                    if isinstance(sv, int):
                        size = sv
                    f46 = _fields(_first_msg(f4, 6))
                    hb = f46.get(1, [None])[0]
                    if isinstance(hb, (bytes, bytearray)) and len(hb) == 32:
                        h = hb.hex()
                        hash_sizes[h].add(size)
                mv = top.get(13, top.get(3, [None]))[0]
                if isinstance(mv, int) and mv > 0:
                    mtime = float(mv)
            self.info[itemid] = (is_file, size, h, mtime)
        con.close()
        # Poisoned-hash guard (invariant, not heuristic): a genuine SHA-256
        # uniquely determines size, so one hash at >1 sizes is a sentinel.
        self.poisoned = {h for h, sizes in hash_sizes.items() if len(sizes) > 1}
        self._dircache = {}

    def resolve_dir(self, abspath):
        ap = os.path.realpath(abspath)
        if ap in self._dircache:
            return self._dircache[ap]
        rel = os.path.relpath(ap, self.root)
        comps = [] if rel == "." else [nfc(c) for c in rel.split(os.sep)]
        if rel.startswith("..") or not comps:
            self._dircache[ap] = None
            return None
        found = None
        for cand in self._byname.get(comps[-1], []):
            parts = []
            cur = cand
            seen = set()
            while cur in self.parent and cur not in seen:
                seen.add(cur)
                d, f = self.parent[cur]
                parts.append(f)
                cur = d
            parts.reverse()
            parts = [p for p in parts if p]   # strip empty account-root node
            if parts == comps:
                found = cand
                break
        self._dircache[ap] = found
        return found

    def hash_for_child(self, dirid, filename):
        """(hash, poisoned?) for a file child of resolved dir `dirid`."""
        fn = nfc(filename)
        for cid in self.children.get(dirid, []):
            if self.parent[cid][1] == fn:
                is_file, _size, h, _m = self.info[cid]
                if is_file and h:
                    return h, h in self.poisoned
        return None, False


def make_dbx(root, workdir):
    """DropboxDB for `root`, or None. Never raises — a missing/unreadable DB
    degrades to fs-walk-only coverage (a warning is printed)."""
    dbx_root = dropbox_root_for(root)
    if not dbx_root:
        return None
    if not os.path.exists(nucleus_path()):
        out("  warning: Dropbox sync database not found — cloud-only files "
            "will have no content hashes and dataless folders cannot be "
            "backfilled. Is Dropbox running?")
        return None
    try:
        return DropboxDB(workdir, dbx_root)
    except (sqlite3.Error, OSError) as e:
        out(f"  warning: could not read the Dropbox sync database ({e}) — "
            "continuing with filesystem data only.")
        return None


def is_cloud_path(path):
    rp = os.path.realpath(path)
    roots = [cloudstorage_root(),
             os.path.expanduser("~/Dropbox"),
             os.path.expanduser("~/Library/Mobile Documents")]  # iCloud Drive
    return any(rp == r or rp.startswith(r + os.sep) for r in roots)


# ---------------- target guardrail ------------------------------------------
def refuse_target(folder):
    """Return a reason string when `folder` must not be scanned, else None.
    The scan is read-only, so the list is about sanity (system roots,
    the bare filesystem) rather than data safety."""
    rp = os.path.realpath(os.path.abspath(os.path.expanduser(folder)))
    home = os.path.realpath(os.path.expanduser("~"))
    if rp == "/":
        return "refusing to scan the entire filesystem root"
    parent = os.path.dirname(rp)
    if parent == "/" and rp not in ("/Users",):
        # /System /Library /private /usr /Applications /Volumes ... — system
        # roots are permission storms full of files the user does not own.
        return f"refusing the system root {safe(rp)} — point at a folder in your home or cloud storage"
    if rp in ("/Users", "/Volumes"):
        return f"refusing {safe(rp)} — pick a specific folder inside it"
    if rp == os.path.join(home, "Library"):
        return "refusing ~/Library — it is app-managed internals, not user content"
    if not os.path.isdir(rp):
        return f"not a folder: {safe(rp)}"
    return rp if False else None


def auto_excludes(root):
    """Subtrees silently skipped (recorded as excluded) inside a scan root."""
    home = os.path.realpath(os.path.expanduser("~"))
    ex = set()
    if os.path.realpath(root) == home:
        ex.add(os.path.join(home, "Library"))
    return ex


# ---------------- SQLite index ----------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, val TEXT);
CREATE TABLE IF NOT EXISTS dirs(
  id INTEGER PRIMARY KEY,
  parent INTEGER,
  name TEXT, name_b BLOB,
  dev INTEGER, ino INTEGER,
  mtime REAL, btime REAL, flags INTEGER,
  depth INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  err INTEGER,
  zone TEXT,
  prot INTEGER,
  empty INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS dirs_parent ON dirs(parent);
CREATE INDEX IF NOT EXISTS dirs_status ON dirs(status);
CREATE TABLE IF NOT EXISTS files(
  parent INTEGER NOT NULL,
  name TEXT, name_b BLOB,
  size INTEGER, blocks INTEGER,
  mtime REAL, btime REAL,
  mode INTEGER, flags INTEGER, nlink INTEGER,
  ino INTEGER, dev INTEGER,
  dataless INTEGER NOT NULL DEFAULT 0,
  hl_first INTEGER NOT NULL DEFAULT 1,
  link_target TEXT,
  src TEXT NOT NULL DEFAULT 'fs',
  hash TEXT,
  poisoned INTEGER NOT NULL DEFAULT 0,
  cls TEXT
);
CREATE INDEX IF NOT EXISTS files_parent ON files(parent);
CREATE INDEX IF NOT EXISTS files_hash ON files(hash);
"""
# files.src: 'fs' = seen by the walker; 'nucleus' = backfilled from Dropbox's
# DB because the parent dir was dataless-unenumerated (kill switch working as
# designed). files.hash: nucleus SHA-256, recorded ONLY for dataless files —
# a materialized file's DB hash can be stale (20-differing-bytes twin lesson)
# and teedaa's scan never reads content, so local bytes stay hashless here.

# dir status values:
#   pending    discovered, children not yet recorded (resume restarts here)
#   done       fully enumerated
#   error      scandir failed (errno recorded in err)
#   dataless   enumeration would have downloaded (EDEADLK) — nucleus backfill
#   foreign    other device/volume (firmlink, mount, other provider) — not walked
#   too-deep   beyond DEPTH_CAP — recorded, not walked
#   excluded   auto-excluded subtree (e.g. ~/Library when scanning ~)


def index_path_for(root):
    h = hashlib.sha1(os.path.realpath(root).encode("utf-8", "surrogatepass")).hexdigest()[:12]
    d = os.path.expanduser("~/.cache/teedaa")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"index-{h}.sqlite3")


def open_index(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA temp_store=MEMORY")
    # Version check BEFORE the schema script: running v3 DDL (e.g. an index
    # on files.hash) against v2 tables explodes — old indexes are a cache,
    # so an outdated one is simply rebuilt from scratch.
    try:
        row = con.execute("SELECT val FROM meta WHERE key='schema'").fetchone()
        ver = row[0] if row else None
    except sqlite3.OperationalError:
        ver = None                      # brand-new database
    if ver != SCHEMA_VERSION:
        # covers pre-versioning indexes too (ver None, tables present)
        con.executescript("DROP TABLE IF EXISTS files; "
                          "DROP TABLE IF EXISTS dirs; "
                          "DROP TABLE IF EXISTS meta;")
    con.executescript(SCHEMA)
    if ver != SCHEMA_VERSION:
        meta_set(con, "schema", SCHEMA_VERSION)
        con.commit()
    return con


def meta_get(con, key, default=None):
    row = con.execute("SELECT val FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def meta_set(con, key, val):
    con.execute("INSERT OR REPLACE INTO meta(key,val) VALUES(?,?)", (key, str(val)))


def name_cols(rawname):
    """(text, blob) storage for a filename. Hostile bytes that cannot encode
    as strict UTF-8 keep their exact bytes in the BLOB column; the TEXT column
    then holds a lossy rendering for display only."""
    disp = nfc(rawname)
    try:
        disp.encode("utf-8", "strict")
        return disp, None
    except UnicodeEncodeError:
        return disp.encode("utf-8", "replace").decode("utf-8"), os.fsencode(rawname)


# ---------------- nucleus enrichment ----------------------------------------
def _dir_paths(con, root):
    """dir_id -> absolute path for every dir row (iterative, memoized)."""
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


def nucleus_enrich(con, root):
    """Post-walk enrichment from Dropbox's local DB — zero provider traffic:
    (1) backfill the contents of dirs the kill switch refused to enumerate
    (status 'dataless'), (2) record content hashes for placeholder files the
    walker did see. Idempotent: prior backfill rows are replaced."""
    stats = {"nucleus_used": False, "backfilled_dirs": 0,
             "backfilled_files": 0, "hashes_filled": 0, "poisoned": 0,
             "unresolved_dirs": 0}
    workdir = tempfile.mkdtemp(prefix="teedaa-nucleus-")
    try:
        return _nucleus_enrich_inner(con, root, workdir, stats)
    finally:
        # Always remove the snapshot (can be hundreds of MB) — even on a
        # crash/SIGINT mid-enrich. (audit: workdir leak)
        shutil.rmtree(workdir, ignore_errors=True)


def _nucleus_enrich_inner(con, root, workdir, stats):
    dbx = make_dbx(root, workdir)
    if dbx is None:
        return stats
    stats["nucleus_used"] = True
    con.execute("DELETE FROM files WHERE src='nucleus'")
    con.execute("DELETE FROM dirs WHERE status='backfilled'")
    paths = _dir_paths(con, root)

    # (1) backfill dataless-unenumerated subtrees from the nucleus tree
    dataless_dirs = [d for d, in con.execute(
        "SELECT id FROM dirs WHERE status='dataless'")]
    for did in dataless_dirs:
        itemid = dbx.resolve_dir(paths[did])
        if itemid is None:
            stats["unresolved_dirs"] += 1
            continue
        stack = [(itemid, did, 0)]
        seen = set()
        while stack:
            iid, parent_rowid, depth = stack.pop()
            if iid in seen or depth >= DEPTH_CAP:
                continue
            seen.add(iid)
            for cid in dbx.children.get(iid, []):
                is_file, size, h, mtime = dbx.info[cid]
                cname = dbx.parent[cid][1]
                if is_file:
                    poisoned = 1 if (h and h in dbx.poisoned) else 0
                    con.execute(
                        "INSERT INTO files(parent,name,size,mtime,dataless,"
                        "src,hash,poisoned) VALUES(?,?,?,?,1,'nucleus',?,?)",
                        (parent_rowid, cname, size, mtime,
                         None if poisoned else h, poisoned))
                    stats["backfilled_files"] += 1
                    stats["poisoned"] += poisoned
                else:
                    cur = con.execute(
                        "INSERT INTO dirs(parent,name,mtime,depth,status) "
                        "VALUES(?,?,?,?,'backfilled')",
                        (parent_rowid, cname, mtime,
                         (con.execute("SELECT depth FROM dirs WHERE id=?",
                                      (parent_rowid,)).fetchone()[0] or 0) + 1))
                    stats["backfilled_dirs"] += 1
                    stack.append((cid, cur.lastrowid, depth + 1))
    con.commit()

    # (2) content hashes for placeholder files the walker recorded.
    # ONLY dataless files: a materialized file's DB hash can be stale.
    by_parent = defaultdict(list)
    for rowid, parent, name in con.execute(
            "SELECT rowid, parent, name FROM files "
            "WHERE dataless=1 AND src='fs' AND hash IS NULL"):
        by_parent[parent].append((rowid, name))
    updates = []
    for parent, rows in by_parent.items():
        dirid = dbx.resolve_dir(paths.get(parent, ""))
        if dirid is None:
            continue
        for rowid, name in rows:
            h, poisoned = dbx.hash_for_child(dirid, name)
            if h is None:
                continue
            if poisoned:
                updates.append((None, 1, rowid))
                stats["poisoned"] += 1
            else:
                updates.append((h, 0, rowid))
                stats["hashes_filled"] += 1
    con.executemany("UPDATE files SET hash=?, poisoned=? WHERE rowid=?",
                    updates)
    con.commit()
    return stats                # workdir cleanup is in nucleus_enrich's finally


# ---------------- scanner ---------------------------------------------------
class Progress:
    def __init__(self, quiet):
        self.quiet = quiet
        self.t0 = time.monotonic()
        self.last = 0.0
        self.rate = 0.0
        self.last_entries = 0
        self.dirs_done = 0
        self.dirs_found = 1
        self.files = 0
        self.bytes = 0
        self.dataless = 0
        self.errors = 0
        self.force = False
        if IS_MACOS and hasattr(signal, "SIGINFO"):
            try:
                signal.signal(signal.SIGINFO, self._siginfo)
            except (OSError, ValueError):
                pass

    def _siginfo(self, *_):
        self.force = True

    def tick(self):
        now = time.monotonic()
        if not self.force and (self.quiet or now - self.last < PROGRESS_EVERY):
            return
        entries = self.files + self.dirs_done
        dt = max(now - self.last, 1e-6)
        inst = (entries - self.last_entries) / dt if self.last else 0.0
        self.rate = inst if not self.rate else 0.9 * self.rate + 0.1 * inst
        self.last = now
        self.last_entries = entries
        self.force = False
        el = int(now - self.t0)
        line = (f"\r  scanning: {self.dirs_done}/{self.dirs_found} folders · "
                f"{self.files:,} files · {human(self.bytes)} · "
                f"{self.dataless:,} cloud-only · {self.rate:,.0f}/s · "
                f"{el // 60}m{el % 60:02d}s ")
        print(line, end="", file=sys.stderr, flush=True)

    def close(self):
        if not self.quiet:
            print("", file=sys.stderr)


def cmd_scan(args):
    root = args.folder
    reason = refuse_target(root)
    if reason:
        out(f"REFUSED: {reason}.")
        emit_json({"cmd": "scan", "ok": False, "refused": reason})
        return 3
    root = os.path.realpath(root)

    armed = arm_killswitch()
    if not armed and is_cloud_path(root):
        out("REFUSED: cannot arm the no-download kill switch on this system, "
            "and the target is cloud storage — scanning could trigger "
            "downloads, so teedaa will not proceed.")
        emit_json({"cmd": "scan", "ok": False,
                   "refused": "killswitch unavailable for cloud target"})
        return 3
    if not armed:
        out("  note: no-download kill switch unavailable (not macOS?) — "
            "target is local, continuing.")

    ipath = args.index or index_path_for(root)
    con = open_index(ipath)
    prev_root = meta_get(con, "root")
    complete = meta_get(con, "scan_complete") == "1"
    if prev_root is not None and prev_root != root:
        con.close()
        out(f"REFUSED: index {safe(ipath)} belongs to {safe(prev_root)} — "
            "pass a different --index or remove it.")
        emit_json({"cmd": "scan", "ok": False, "refused": "index/root mismatch"})
        return 3
    if prev_root == root and complete and not args.rescan:
        con.close()
        out("  a completed index for this folder already exists — "
            "use --rescan to walk again, or run `report` now.")
        emit_json({"cmd": "scan", "ok": True, "index": ipath,
                   "reused_existing": True})
        return 0
    if args.rescan and prev_root == root:
        # Keep the schema row — open_index set it, nothing re-seeds it, and a
        # missing 'schema' makes the NEXT open_index treat this fresh index as
        # stale and drop everything just walked. (audit FS-de8a6f23)
        con.executescript("DELETE FROM files; DELETE FROM dirs; "
                          "DELETE FROM meta WHERE key != 'schema';")
        con.commit()
        prev_root = None

    try:
        st_root = os.stat(root, follow_symlinks=False)
    except OSError as e:
        con.close()
        out(f"REFUSED: cannot stat the target ({e}).")
        emit_json({"cmd": "scan", "ok": False, "refused": "stat failed"})
        return 3
    root_dev = st_root.st_dev

    excludes = auto_excludes(root)
    prog = Progress(args.quiet)
    interrupted = False

    # -- seed or resume ------------------------------------------------------
    stack = []  # (dir_id, abspath, depth)
    if prev_root == root:
        # Resume: pending dirs have no recorded children (children + the flip
        # to done are one transaction), but delete defensively, then re-seed.
        con.execute("DELETE FROM files WHERE parent IN "
                    "(SELECT id FROM dirs WHERE status='pending')")
        con.execute("DELETE FROM dirs WHERE parent IN "
                    "(SELECT id FROM dirs WHERE status='pending')")
        rows = con.execute("SELECT id, depth FROM dirs WHERE status='pending'").fetchall()
        idmap = {}
        for did, pname, pparent in con.execute(
                "SELECT id, name, parent FROM dirs"):
            idmap[did] = (pname, pparent)

        def rebuild(did):
            parts = []
            cur = did
            seen = set()
            while cur is not None and cur not in seen:
                seen.add(cur)
                nm, par = idmap[cur]
                if par is None:
                    break
                parts.append(nm)
                cur = par
            parts.reverse()
            return os.path.join(root, *parts) if parts else root
        for did, depth in rows:
            stack.append((did, rebuild(did), depth))
        done_before = con.execute(
            "SELECT COUNT(*) FROM dirs WHERE status='done'").fetchone()[0]
        prog.dirs_done = done_before
        prog.dirs_found = done_before + len(stack)
        prog.files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        prog.bytes = con.execute(
            "SELECT COALESCE(SUM(size),0) FROM files WHERE hl_first=1").fetchone()[0]
        prog.dataless = con.execute(
            "SELECT COUNT(*) FROM files WHERE dataless=1").fetchone()[0]
        out(f"  resuming: {len(stack)} folders left over from the "
            f"interrupted scan of {safe(root)}")
        # Do NOT set scan_complete here even when the walk is done — the shared
        # post-walk bookkeeping sets it only AFTER nucleus_enrich runs, so a
        # crash during enrich can't leave a 'complete' index missing all cloud
        # backfill. An empty stack simply falls through to that path.
        # (audit: resume scan_complete early)
    else:
        meta_set(con, "root", root)
        meta_set(con, "root_dev", root_dev)
        meta_set(con, "engine_version", __version__)
        meta_set(con, "started_epoch", int(time.time()))
        meta_set(con, "scan_complete", "0")
        nm, nb = name_cols(os.path.basename(root) or root)
        cur = con.execute(
            "INSERT INTO dirs(parent,name,name_b,dev,ino,mtime,btime,flags,"
            "depth,status) VALUES(NULL,?,?,?,?,?,?,?,0,'pending')",
            (nm, nb, st_root.st_dev, st_root.st_ino, st_root.st_mtime,
             getattr(st_root, "st_birthtime", None),
             getattr(st_root, "st_flags", 0)))
        con.commit()
        stack.append((cur.lastrowid, root, 0))
        prog.dirs_found = 1

    # -- walk ----------------------------------------------------------------
    # Multi-link inodes already recorded as first-sight must survive a resume,
    # or the re-walked subtree re-marks the same inode hl_first and its bytes
    # double-count (caught live in the item-2 smoke test: 43 B became 49 B).
    hl_seen = set()          # packed dev<<40|ino for multi-link inodes
    if prev_root == root:
        for dev, ino in con.execute(
                "SELECT dev, ino FROM files WHERE nlink>1 AND hl_first=1"):
            if dev is not None and ino is not None:
                hl_seen.add((dev << 40) | ino)
    frows = []               # buffered file rows
    pending_done = []        # dir ids to flip once their rows are buffered
    last_commit = time.monotonic()

    def flush():
        nonlocal frows, pending_done, last_commit
        if frows:
            con.executemany(
                "INSERT INTO files(parent,name,name_b,size,blocks,mtime,btime,"
                "mode,flags,nlink,ino,dev,dataless,hl_first,link_target) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", frows)
            frows = []
        if pending_done:
            con.executemany("UPDATE dirs SET status='done' WHERE id=?",
                            [(d,) for d in pending_done])
            pending_done = []
        con.commit()
        last_commit = time.monotonic()

    try:
        while stack:
            dir_id, dpath, depth = stack.pop()
            if dpath in excludes:
                con.execute("UPDATE dirs SET status='excluded' WHERE id=?",
                            (dir_id,))
                continue
            if depth >= DEPTH_CAP:
                con.execute("UPDATE dirs SET status='too-deep' WHERE id=?",
                            (dir_id,))
                continue
            try:
                entries = list(os.scandir(dpath))
            except OSError as e:
                status = "dataless" if e.errno == errno.EDEADLK else "error"
                con.execute("UPDATE dirs SET status=?, err=? WHERE id=?",
                            (status, e.errno, dir_id))
                prog.errors += 1
                prog.dirs_done += 1
                continue
            for entry in entries:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError as e:
                    prog.errors += 1
                    nm, nb = name_cols(entry.name)
                    frows.append((dir_id, nm, nb, None, None, None, None,
                                  None, None, None, None, None, 0, 1, None))
                    continue
                mode = st.st_mode
                if statmod.S_ISDIR(mode):
                    nm, nb = name_cols(entry.name)
                    if st.st_dev != root_dev or \
                            (getattr(st, "st_flags", 0) & SF_FIRMLINK):
                        status = "foreign"
                    else:
                        status = "pending"
                    cur = con.execute(
                        "INSERT INTO dirs(parent,name,name_b,dev,ino,mtime,"
                        "btime,flags,depth,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (dir_id, nm, nb, st.st_dev, st.st_ino, st.st_mtime,
                         getattr(st, "st_birthtime", None),
                         getattr(st, "st_flags", 0), depth + 1, status))
                    prog.dirs_found += 1
                    if status == "pending":
                        stack.append((cur.lastrowid, entry.path, depth + 1))
                    else:
                        prog.dirs_done += 1
                    continue
                # file / symlink / other
                nm, nb = name_cols(entry.name)
                dataless = 1 if st_is_dataless(st) else 0
                link_target = None
                if statmod.S_ISLNK(mode):
                    try:
                        link_target = os.readlink(entry.path)
                    except OSError:
                        link_target = ""
                hl_first = 1
                if st.st_nlink > 1 and statmod.S_ISREG(mode):
                    key = (st.st_dev << 40) | st.st_ino
                    if key in hl_seen:
                        hl_first = 0
                    else:
                        hl_seen.add(key)
                frows.append((dir_id, nm, nb, st.st_size,
                              getattr(st, "st_blocks", 0), st.st_mtime,
                              getattr(st, "st_birthtime", None), mode,
                              getattr(st, "st_flags", 0), st.st_nlink,
                              st.st_ino, st.st_dev, dataless, hl_first,
                              link_target))
                prog.files += 1
                if hl_first:
                    prog.bytes += st.st_size
                prog.dataless += dataless
            pending_done.append(dir_id)
            prog.dirs_done += 1
            if len(frows) >= COMMIT_ROWS or \
                    time.monotonic() - last_commit > COMMIT_SECS:
                flush()
            prog.tick()
        flush()
    except KeyboardInterrupt:
        interrupted = True
        flush()
    finally:
        prog.close()

    nstats = {}
    if not interrupted:
        nstats = nucleus_enrich(con, root)
        meta_set(con, "scan_complete", "1")
        meta_set(con, "finished_epoch", int(time.time()))
        con.commit()

    # Final totals come from the index (authoritative after backfill).
    tot_files, tot_bytes = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(size),0) FROM files WHERE hl_first=1"
    ).fetchone()
    tot_dataless = con.execute(
        "SELECT COUNT(*) FROM files WHERE dataless=1").fetchone()[0]
    dur = int(time.monotonic() - prog.t0)
    result = {"cmd": "scan", "ok": True, "root": root, "index": ipath,
              "interrupted": interrupted,
              "dirs": prog.dirs_done, "files": tot_files,
              "bytes_logical": tot_bytes, "dataless": tot_dataless,
              "errors": prog.errors, "seconds": dur,
              "killswitch": bool(armed)}
    result.update(nstats)
    con.close()
    if interrupted:
        out("  interrupted — progress saved; re-run the same command to resume.")
    else:
        extra = ""
        if nstats.get("backfilled_files"):
            extra = (f" · {nstats['backfilled_files']:,} files listed from "
                     "Dropbox's own records (folders the no-download guard "
                     "kept closed)")
        out(f"  scan complete: {tot_files:,} files in {prog.dirs_done:,} "
            f"folders · {human(tot_bytes)} logical · "
            f"{tot_dataless:,} cloud-only placeholders · {dur}s{extra}")
    emit_json(result)
    return 0


# ---------------- report -----------------------------------------------------
def _load_ready_index(args, need_classify=True):
    """Open the completed index for args.folder (classifying if not yet).
    Returns (con, root, ipath) or (None, error_message, None)."""
    root = os.path.realpath(args.folder)
    # Arm the no-download kill switch here too: classify runs lstat-class
    # probes on symlink targets (classify.py J10), and report/plan reach this
    # path without ever touching cmd_scan's arming — the policy is per-process.
    # (audit FS-8540b417)
    armed = arm_killswitch()
    if not armed and is_cloud_path(root):
        return None, ("cannot arm the no-download guard on this system and the "
                      "target is cloud storage — refusing so nothing downloads"), None
    ipath = args.index or index_path_for(root)
    if not os.path.exists(ipath):
        return None, "no index yet — run `scan` first", None
    con = open_index(ipath)
    if meta_get(con, "root") != root:
        con.close()
        return None, "index belongs to a different folder — run `scan`", None
    if meta_get(con, "scan_complete") != "1":
        con.close()
        return None, "scan is incomplete — re-run `scan` to finish it", None
    if need_classify and meta_get(con, "classified") != SCHEMA_VERSION:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import classify
        out("  classifying (protected zones, junk, photos, sensitive)...")
        classify.classify_index(con, root)
        meta_set(con, "classified", SCHEMA_VERSION)
        con.commit()
    return con, root, ipath


def _report_dir(root):
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(root)) or "root"
    d = os.path.join(os.path.expanduser("~/.cache/teedaa/reports"),
                     f"{base}-{time.strftime('%Y%m%d-%H%M')}")
    os.makedirs(d, exist_ok=True)
    return d


def _pct(part, whole):
    return f"{100.0 * part / whole:.1f}%" if whole else "0%"


def render_markdown(agg):
    """Beginner-readable report per the house rule: plain-English lead lines,
    jargon glossed on first use, aggregates over raw dumps."""
    t = agg["totals"]
    L = []
    add = L.append
    junk_ab = sum(r["bytes"] for r in agg["junk"] if r["tier"] in "AB")
    junk_ab_n = sum(r["count"] for r in agg["junk"] if r["tier"] in "AB")
    add(f"# Folder audit — {agg['root']}")
    add("")
    add(f"*Scanned {time.strftime('%Y-%m-%d %H:%M', time.localtime(agg['generated']))} · "
        f"{t['files']:,} files in {t['dirs']:,} folders · engine v{__version__} · "
        "read-only: nothing was opened, downloaded, moved, or changed.*")
    add("")
    add("## The headline")
    add("")
    if t["physical"] < t["bytes"] * 0.95:
        add(f"- **Total content: {human(t['bytes'])}** (logical size — what "
            f"the files claim to be; only {human(t['physical'])} of it is "
            "physically on this Mac's disk, the rest lives in the cloud).")
    else:
        add(f"- **Total content: {human(t['bytes'])}** "
            f"(≈{human(t['physical'])} on disk).")
    if t["dataless_files"]:
        add(f"- **{t['dataless_files']:,} files ({human(t['dataless_bytes'])}) "
            "are cloud-only placeholders** — their bytes live on Dropbox's "
            "servers, not on this Mac. teedaa never downloads them.")
    add(f"- **About {human(junk_ab)} across {junk_ab_n:,} items looks like "
        "removable dead weight** (certain + probable junk). Nothing has been "
        "touched — see the plan for the approval checklist.")
    if agg["dupes"]["groups"]:
        add(f"- **{human(agg['dupes']['wasted_bytes'])} is duplicate overlap** "
            f"({agg['dupes']['groups']:,} groups of byte-identical cloud files "
            "— found via Dropbox's own content fingerprints, no downloads). "
            "The /check-for-dupes skill is the precision tool for these.")
    add("")
    add("## What we will NOT touch (protected)")
    add("")
    add("These were auto-detected and excluded from every suggestion:")
    add("")
    for z in agg["protected"][:15]:
        add(f"- `{safe(z['path'])}` — {z['kind']} · {human(z['bytes'])} · "
            f"{z['files']:,} files")
    more = len(agg["protected"]) - 15
    if more > 0:
        add(f"- …and {more} more protected zones (full list in the dashboard)")
    if agg["sensitive"]:
        s1 = sum(r["count"] for r in agg["sensitive"] if r["sev"] == "S1")
        s2 = sum(r["count"] for r in agg["sensitive"] if r["sev"] == "S2")
        add("")
        add(f"Also excluded: **{s1:,} sensitive-looking files** (IDs, "
            f"financial, credentials, private exports) plus {s2:,} borderline "
            "ones — detected by NAME only, never opened, never in any cleanup "
            "plan. Details are in a separate discreet section at the end.")
    add("")
    add("## Where the space goes")
    add("")
    add("| Category | Size | Files | Share |")
    add("|---|---:|---:|---:|")
    rows = sorted(zip(agg["cats"], agg["cat_totals"]), key=lambda r: -r[1][0])
    for cat, (b, c) in rows:
        if c:
            add(f"| {cat} | {human(b)} | {c:,} | {_pct(b, t['bytes'])} |")
    add("")
    add("## Quick wins (few decisions, big space)")
    add("")
    for r in agg["junk"][:10]:
        tier_word = {"A": "🟢 safe", "B": "🟡 review",
                     "C": "🔵 look first"}[r["tier"]]
        add(f"- **{FAMILY_NAMES.get(r['family'], r['family'])}** — "
            f"{human(r['bytes'])} across {r['count']:,} items ({tier_word}). "
            f"Examples: {', '.join('`' + safe_md(s) + '`' for s in r['samples'][:3])}")
    if agg["reclaim"]:
        add("")
        add("Folders with the most removable weight:")
        add("")
        for r in agg["reclaim"][:10]:
            add(f"- `{safe(r['path'])}` — {human(r['junk_bytes'])} junk inside")
    if agg["caches"]:
        cb = sum(c["bytes"] for c in agg["caches"])
        add("")
        add(f"Rebuildable space (caches, `node_modules`, build products): "
            f"**{human(cb)}** — safe to regenerate, listed separately from "
            "junk because active projects need them.")
    add("")
    add("## How old is this stuff?")
    add("")
    add("Old ≠ junk: your 1998 photos are treasure; a 1998 cache file is not. "
        "By file-modified year:")
    add("")
    years = sorted(int(y) for y in agg["ageByYear"])
    if years:
        for y in years:
            b, c = agg["ageByYear"][str(y)]
            add(f"- {y}: {human(b)} ({c:,} files)")
    add("")
    if agg["empty_dirs"]:
        add(f"**Empty folders:** {agg['empty_dirs']:,} folders contain "
            "nothing at all (or only system droppings).")
        add("")
    add("## Sensitive items (discreet summary — names not listed)")
    add("")
    if agg["sensitive"]:
        add("| Kind | Certainty | Count | Where (folders) |")
        add("|---|---|---:|---|")
        for r in agg["sensitive"]:
            kind = SENS_NAMES.get(r["cat"], r["cat"])
            sev = "definite" if r["sev"] == "S1" else "borderline"
            dirs = "; ".join(f"`{safe(d)}`" for d in r["dirs"][:2]) or "—"
            add(f"| {kind} | {sev} | {r['count']:,} | {dirs} |")
        add("")
        add("These are NEVER included in cleanup suggestions. Borderline "
            "items fail closed: treated as sensitive until you look.")
    else:
        add("None detected by filename patterns.")
    add("")
    add("## Method (plain English)")
    add("")
    add("- teedaa read only names, sizes, dates, and flags — it never opened "
        "a single file's contents.")
    add("- An OS-level switch made downloads impossible during the scan: any "
        "attempt would have failed loudly instead of pulling bytes.")
    add("- Cloud-only folders were inventoried from Dropbox's own local "
        "records (the same trusted source its desktop app uses).")
    add(f"- Scan errors/permission denials: {t['errors']:,}.")
    return "\n".join(L) + "\n"


FAMILY_NAMES = {
    "J1": "System droppings (.DS_Store & co)",
    "J2": "Abandoned partial downloads",
    "J3": "Editor/Office temp & lock files",
    "J5": "Crash logs & dumps",
    "J6": "Installers (apps already installed or obsolete)",
    "J7": "Archives with an extracted copy next to them",
    "J8": "Duplicate-name copies & sync conflicts",
    "J9": "Zero-byte files",
    "J10": "Broken shortcuts/symlinks",
    "J13": "Old rotated logs & patch leftovers",
    "SC": "Orphaned photo-edit sidecars",
}
SENS_NAMES = {"cred": "Passwords & keys", "fin": "Financial documents",
              "id": "Identity documents", "med": "Medical records",
              "leg": "Legal documents", "int": "Private media",
              "bak": "Chat/mail exports & backups", "cry": "Crypto wallets"}


def cmd_report(args):
    con, root_or_err, ipath = _load_ready_index(args)
    if con is None:
        out(f"REFUSED: {root_or_err}.")
        emit_json({"cmd": "report", "ok": False, "refused": root_or_err})
        return 3
    root = root_or_err
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import aggregate as agg_mod
    out("  aggregating...")
    agg = agg_mod.aggregate(con, root)
    con.close()
    outdir = _report_dir(root)
    md_path = os.path.join(outdir, "report.md")
    write_atomic(md_path, render_markdown(agg))
    data_path = os.path.join(outdir, "data.json")
    write_atomic(data_path, json.dumps(agg))
    html_path = None
    tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "templates", "dashboard.html")
    if os.path.exists(tpl):
        with open(tpl) as f:
            html = f.read()
        # Escape EVERY '<' in the embedded JSON to its \\u003c form. This is
        # the one bullet-proof breakout defense: it kills </script (any
        # case), <!-- and <![CDATA[ at once, and stays valid JSON. json.dumps
        # (ensure_ascii=True) already ASCII-escapes U+2028/29. (SEC-9f42da4f)
        payload = json.dumps(agg).replace("<", "\\u003c")
        html = html.replace("__TEEDAA_DATA__", payload)
        html_path = os.path.join(outdir, "dashboard.html")
        write_atomic(html_path, html)
    t = agg["totals"]
    out(f"  report ready: {t['files']:,} files, {human(t['bytes'])} — "
        f"{safe(outdir)}")
    emit_json({"cmd": "report", "ok": True, "root": root, "outdir": outdir,
               "report_md": md_path, "dashboard_html": html_path,
               "data_json": data_path,
               "files": t["files"], "bytes": t["bytes"],
               "junk_bytes": sum(r["bytes"] for r in agg["junk"]
                                 if r["tier"] in "AB"),
               "dupe_wasted": agg["dupes"]["wasted_bytes"],
               "sensitive_files": sum(r["count"] for r in agg["sensitive"])})
    return 0


# ---------------- plan -------------------------------------------------------
def plans_dir_for(root):
    h = hashlib.sha1(os.path.realpath(root).encode(
        "utf-8", "surrogatepass")).hexdigest()[:12]
    d = os.path.expanduser(f"~/.cache/teedaa/plans/{h}")
    os.makedirs(d, exist_ok=True)
    return d


def cmd_plan(args):
    con, root_or_err, ipath = _load_ready_index(args)
    if con is None:
        out(f"REFUSED: {root_or_err}.")
        emit_json({"cmd": "plan", "ok": False, "refused": root_or_err})
        return 3
    root = root_or_err
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import planner
    out("  building plans (nothing will be executed)...")
    plans = planner.build_plans(con, root, __version__)
    con.close()
    pdir = plans_dir_for(root)
    written = []
    for p in plans:
        path = os.path.join(pdir, p["plan_id"] + ".json")
        write_atomic(path, json.dumps(p, indent=1))
        written.append({"plan_id": p["plan_id"], "type": p["type"],
                        "tier": p["tier"], "ops": p["op_count"],
                        "bytes": p["total_bytes"], "consent": p["consent"],
                        "warnings": p["warnings"], "file": path})
        out(f"  {p['tier']} {p['type']}: {p['op_count']:,} moves · "
            f"{human(p['total_bytes'])} · consent: {p['consent']} · "
            f"{safe(path)}")
    if not written:
        out("  nothing to propose — the tree is clean at plan level.")
    out("  NOTHING has been moved. Executing a plan requires: "
        "apply --plan <file> --approve <plan_id>")
    emit_json({"cmd": "plan", "ok": True, "root": root, "plans": written})
    return 0


# ---------------- apply / verify / undo (the only mutating paths) -----------
def cmd_apply(args):
    root = os.path.realpath(args.folder)
    arm_killswitch()      # arm BEFORE reading the plan file, in case it (or
                          # its dir) is itself a cloud placeholder. (Low fix)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import executor
    if not args.approve:
        out("REFUSED: apply needs BOTH --plan <file> AND --approve "
            "<plan_id> — the id inside the plan file, echoed back "
            "deliberately. This is rule zero's double gate.")
        emit_json({"cmd": "apply", "ok": False, "refused": "no approval"})
        return 3
    plan, err = executor.load_plan(args.plan, root, args.approve)
    if err:
        out(f"REFUSED: {err}.")
        emit_json({"cmd": "apply", "ok": False, "refused": err})
        return 3
    out(f"  applying {plan['plan_id']}: {plan['op_count']:,} moves "
        f"({human(plan['total_bytes'])}) — rename-only, undo available")
    try:
        stats = executor.apply_plan(plan, root,
                                    progress=lambda s: out("  " + s))
    except SystemExit as e:
        out(str(e))
        emit_json({"cmd": "apply", "ok": False, "refused": str(e)})
        return 3
    out(f"  done: {stats['moved']:,} moved · "
        f"{stats['skipped_missing']} vanished-before-move · "
        f"{stats['skipped_changed']} changed-since-plan (left alone) · "
        f"{stats['failed']} failed · manifest + undo.sh at "
        f"{safe(os.path.dirname(stats['manifest']))}")
    result = {"cmd": "apply", "ok": stats["failed"] == 0,
              "plan_id": plan["plan_id"]}
    result.update(stats)
    emit_json(result)
    return 0 if stats["failed"] == 0 else 2


def cmd_verify(args):
    root = os.path.realpath(args.folder)
    arm_killswitch()      # defense-in-depth; verify only lstats (safe anyway)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import executor
    res = executor.verify_plans(root, args.plan_id)
    if not res["plans"]:
        out("  no teedaa manifests found for this folder — nothing was ever "
            "applied here (or it was fully undone and retired).")
        emit_json({"cmd": "verify", "ok": True, "plans": []})
        return 0
    for r in res["plans"]:
        bad = r["dst_missing"] or r["corrupt"] or r.get("unmanifested")
        verdict = "OK" if not bad else "PROBLEM"
        out(f"  {r['plan_id']}: {r['moved_ok']:,} in place · "
            f"{r['already_undone']} restored · {r['dst_missing']} MISSING · "
            f"{r['corrupt']} corrupt · {r.get('unmanifested', 0)} "
            f"unaccounted-for → {verdict}")
    if not res["ok"]:
        out("  PROBLEM: some moved files are at neither location. Do NOT "
            "delete any quarantine folder — investigate first.")
    emit_json({"cmd": "verify", "ok": res["ok"], "plans": res["plans"]})
    return 0 if res["ok"] else 2


def cmd_undo(args):
    root = os.path.realpath(args.folder)
    arm_killswitch()      # defense-in-depth; undo only renames (metadata-only)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import executor
    try:
        res = executor.undo_plans(root, args.plan_id,
                                  progress=lambda s: out("  " + s))
    except SystemExit as e:
        out(str(e))
        emit_json({"cmd": "undo", "ok": False, "refused": str(e)})
        return 3
    if not res["plans"]:
        out("  nothing to undo — no manifests found for this folder.")
    else:
        out(f"  undo complete: {res['restored']:,} files back home · "
            f"{res['leftover']} could not be restored"
            + (" — investigate before deleting anything"
               if res["leftover"] else ""))
    emit_json({"cmd": "undo", "ok": res["leftover"] == 0,
               "restored": res["restored"], "plans": res["plans"]})
    return 0 if res["leftover"] == 0 else 2


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # usage errors exit 64, not 2
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(64)


def main():
    global _JSON_ONLY
    ap = _Parser(description=__doc__,
                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version",
                    version=f"teedaa {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("scan", cmd_scan), ("report", cmd_report),
                     ("plan", cmd_plan), ("apply", cmd_apply),
                     ("verify", cmd_verify), ("undo", cmd_undo)):
        p = sub.add_parser(name,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
        p.add_argument("folder", help="target folder (absolute path recommended)")
        p.add_argument("--json", action="store_true",
                       help="machine mode: only RESULT_JSON on stdout")
        p.add_argument("--quiet", action="store_true",
                       help="suppress progress ticks")
        p.add_argument("--index", default=None,
                       help="override the SQLite index path")
        if name == "scan":
            p.add_argument("--rescan", action="store_true",
                           help="discard the existing index and walk again")
        if name == "apply":
            p.add_argument("--plan", required=True,
                           help="approved plan JSON file to execute")
            p.add_argument("--approve", default=None,
                           help="the plan_id from inside the plan file, "
                                "echoed back as the explicit approval")
        if name in ("verify", "undo"):
            p.add_argument("--plan-id", dest="plan_id", default=None,
                           help="limit to one plan (default: all manifests)")
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    _JSON_ONLY = getattr(args, "json", False)
    if _JSON_ONLY:
        args.quiet = True
    args.folder = os.path.abspath(os.path.expanduser(args.folder))
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
