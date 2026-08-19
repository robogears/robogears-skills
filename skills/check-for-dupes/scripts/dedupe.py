#!/usr/bin/env python3
"""check-for-dupes engine — safe, content-verified folder deduplication.

Duplicates are identified by CONTENT (SHA-256), never by filename. Redundant
copies are MOVED (never deleted) into a "Duplicates" folder, with a manifest
and an undo script. A final `verify` pass proves every quarantined file still
has an identical original outside the Duplicates folder.

Cloud-placeholder aware: files in a Dropbox CloudStorage folder that are
cloud-only (0 blocks on disk) are identified via Dropbox's own recorded
SHA-256 in its local sync database — no download is ever triggered.
Locally-present files are always hashed from their real bytes, which
automatically defeats stale database records.

Placeholder-hash safeguard: a genuine SHA-256 uniquely determines a file's
size, so any hash seen at MORE THAN ONE distinct size cannot be a real content
hash — it is a sentinel/placeholder or corrupt record (Dropbox has been seen
assigning ONE sentinel SHA-256 to dozens of different cloud-only files). Such
"poisoned" hashes are refused everywhere: the affected files are never
quarantined (they are reported, left in place) and `verify` FAILS (exit 2) if
any quarantined file still carries one. This makes the size↔hash disagreement a
hard error, not a warning.

Subcommands:
  scan   <folder> [--scope single|per-folder]     dry run: report what would move
  move   <folder> [--scope ...] [--max-passes N]  quarantine dupes (drain loop)
  verify <folder> [--scope ...]                   security check (read-only)
  where  <folder> [names...]                      per-file truth: list every
                                                  byte-identical copy OUTSIDE
                                                  quarantine (read-only)
  heal   <folder> [--scope ...] [--dry-run]       restore wrongly-quarantined uniques
  undo   <folder>                                 restore everything from manifests

Common flags: --json (machine output only), --quiet (suppress progress),
--recent-secs N (in-flight guard, default 120). `move` also accepts --dry-run
(alias for scan). Pass an absolute path; a relative path beginning with '-'
must be given as './-name'.

Exit codes: 0 = OK (clean or only inconclusive); 2 = verify found real problems;
3 = bad/refused target; 64 = command-line usage error.
"""
import argparse, csv, hashlib, json, os, re, shutil, socket, sqlite3, sys, tempfile, time, unicodedata
from collections import defaultdict, Counter
from contextlib import nullcontext
from urllib.request import pathname2url

__version__ = "0.1.5"

DUP_NAME = "Duplicates"
MANIFEST = "_manifest.csv"
UNDO_SH = "_UNDO_restore_all.sh"
README = "_README.txt"
HEAL_LOG = "_heal_log.csv"
CODE_RESTORE_LOG = "_code_restore_log.csv"   # written by selective code-tree restores
LOCK = ".dedupe.lock"
RESERVED = {MANIFEST.lower(), UNDO_SH.lower(), README.lower(),
            HEAL_LOG.lower(), CODE_RESTORE_LOG.lower(),
            (MANIFEST + ".restored").lower()}
DEFAULT_RECENT_SECS = 120     # skip files modified this recently (active imports)
STALE_LOCK_SECS = 3600        # a lock older than this is considered abandoned
PROGRESS_EVERY = 3.0          # seconds between progress lines

# Candidate Dropbox roots (personal + business/team mounts + legacy ~/Dropbox).
def _dropbox_roots():
    roots = []
    cs = os.path.expanduser("~/Library/CloudStorage")
    if os.path.isdir(cs):
        try:
            for n in os.listdir(cs):
                if n == "Dropbox" or n.startswith("Dropbox"):
                    roots.append(os.path.realpath(os.path.join(cs, n)))
        except OSError:
            pass
    legacy = os.path.expanduser("~/Dropbox")
    if os.path.isdir(legacy):
        roots.append(os.path.realpath(legacy))
    return roots

NUCLEUS = os.path.expanduser("~/.dropbox/instance1/sync_fp/nucleus.sqlite3")

# macOS package bundles that must never be walked into as if they were folders.
BUNDLE_EXTS = (".photoslibrary", ".musiclibrary", ".tvlibrary", ".aplibrary",
               ".pkpass", ".app", ".bundle", ".framework", ".plugin", ".rtfd",
               ".fcpbundle", ".imovielibrary", ".logicx", ".band")

_JSON_ONLY = False   # set by --json: humans lines go to stderr, JSON to stdout

def is_dup_dirname(n):
    return n.lower() == DUP_NAME.lower()

def is_bundle(name):
    low = name.lower()
    return any(low.endswith(ext) for ext in BUNDLE_EXTS)

def nfc(s):
    """Normalize a filename to NFC so macOS NFD names match Dropbox's NFC records."""
    return unicodedata.normalize("NFC", s)

_CTRL = re.compile(r"[\x00-\x1f\x7f]")
def safe(s):
    """Neutralize control characters/newlines in a path before printing it, so a
    hostile filename cannot forge log lines or emit ANSI escapes."""
    return _CTRL.sub("?", str(s))

_CSV_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "'")

def csv_safe(v):
    """Defuse spreadsheet formula injection: a value starting with = + - @ (or a
    lone control char) is prefixed with a quote so Excel/Numbers treats it as
    text. A genuine leading quote is ALSO prefixed (quote-doubling) so the
    encoding is injective: a real file named '-x.jpg can never collide with the
    defused form of -x.jpg, and csv_unsafe can invert deterministically."""
    s = str(v)
    if s and s[0] in _CSV_TRIGGERS:
        return "'" + s
    return s

def csv_unsafe(v):
    """Exact inverse of csv_safe, so restore paths round-trip. csv_safe prepends
    one quote ONLY when the original first character is a trigger (which now
    includes a quote itself), so a stored value beginning with a quote followed
    by a trigger char is a defusing artifact to strip — and nothing else is.
    A legacy row from a writer that did not double genuine quotes ("'x" with a
    non-trigger second char) is left untouched."""
    s = str(v)
    if len(s) >= 2 and s[0] == "'" and s[1] in _CSV_TRIGGERS:
        return s[1:]
    return s

def out(msg):
    """Human-facing line. Goes to stderr in --json mode so stdout stays clean."""
    print(msg, file=(sys.stderr if _JSON_ONLY else sys.stdout))

def emit_json(result):
    print("RESULT_JSON: " + json.dumps(result))   # always stdout

def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}" if u != "B" else f"{int(n)} B"
        n /= 1024.0

UF_COMPRESSED = 0x20         # macOS st_flags bit: transparently decmpfs-compressed
SF_DATALESS = 0x40000000     # macOS st_flags bit: dataless placeholder (File Provider)

def st_is_local(st, path=None):
    """True if the file's data is present on disk (not a cloud placeholder).
    st_blocks is Unix-only; where it is absent (e.g. Windows) assume local.

    A cloud-only placeholder reports st_blocks == 0. macOS decmpfs-compressed
    files (data in extended attributes / a resource fork) ALSO report 0 blocks
    while being fully local — but File Provider implements cloud eviction VIA a
    dataless decmpfs type, so evicted placeholders carry UF_COMPRESSED too.
    SF_DATALESS is therefore checked FIRST and wins: dataless means the bytes
    are NOT here and reading would trigger a download, no matter what other
    flags say. Only a non-dataless decmpfs file is genuinely local."""
    blocks = getattr(st, "st_blocks", None)
    if blocks is None:
        return True
    flags = getattr(st, "st_flags", 0)
    if flags & SF_DATALESS:
        return False
    if blocks > 0:
        return True
    if flags & UF_COMPRESSED:
        return True
    if path is not None and hasattr(os, "listxattr"):
        try:
            if "com.apple.decmpfs" in os.listxattr(path):
                return True
        except OSError:
            pass
    return False

# ---------------- minimal protobuf reader (for Dropbox's nucleus DB) --------
def _rv(b, i):
    r = 0; s = 0
    while True:
        x = b[i]; i += 1
        r |= (x & 0x7F) << s
        if not (x & 0x80):
            return r, i
        s += 7

def _fields(b):
    if not isinstance(b, (bytes, bytearray)):
        return {}
    out_ = {}; i = 0
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
            v = b[i:i + ln]; i += ln
            if len(v) != ln:
                break
        elif w == 5:
            v = b[i:i + 4]; i += 4
        elif w == 1:
            v = b[i:i + 8]; i += 8
        else:
            break
        out_.setdefault(f, []).append(v)
    return out_

def _first_msg(fielddict, key):
    """Return the length-delimited sub-message bytes for `key`, or None if the
    field is absent or was encoded as a non-message wire type (varint/fixed)."""
    if key not in fielddict:
        return None
    v = fielddict[key][0]
    return v if isinstance(v, (bytes, bytearray)) else None

# ---------------- Dropbox sync-DB access ------------------------------------
class DropboxDB:
    """Reads content hashes for cloud-only files out of Dropbox's local
    nucleus.sqlite3 (fp_local_tree). The stored hash is a plain SHA-256 of the
    whole file — same primitive we compute for local files, so both live in
    one namespace. Field 4.6.1 of the protobuf metadata = 32-byte hash."""

    def __init__(self, workdir, dbx_root):
        self.root = dbx_root
        snap = os.path.join(workdir, "nucleus_snapshot.db")
        # Clear any prior snapshot + sidecars so we never pair a fresh main DB
        # with a stale -wal from an earlier copy.
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(snap + suffix)
            except OSError:
                pass
        # Take a transactionally-consistent snapshot with SQLite's online backup
        # API — but with a HARD DEADLINE. Against a busy writer (Dropbox
        # re-indexing after a reboot) an unbounded backup() livelocks: every
        # source write restarts it and sqlite sleeps in nanosleep forever
        # (observed live: two runs asleep 20+ min at 0.0% CPU). If the deadline
        # trips (or backup errors), fall back to copying db+wal+shm together —
        # not transactionally perfect, but WAL replay on open yields a coherent
        # view and it was reliable across all pre-v0.1.4 runs. The source is
        # opened read-only either way so Dropbox's own DB is never perturbed.
        BACKUP_DEADLINE = 45.0
        start = time.monotonic()
        def _deadline(status, remaining, total):
            if time.monotonic() - start > BACKUP_DEADLINE:
                raise TimeoutError("nucleus backup exceeded deadline")
        backed_up = False
        try:
            src_uri = "file:" + pathname2url(NUCLEUS) + "?mode=ro"
            srccon = sqlite3.connect(src_uri, uri=True)
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
            shutil.copy(NUCLEUS, snap)
            for ext in ("-wal", "-shm"):
                try:
                    shutil.copy(NUCLEUS + ext, snap + ext)
                except OSError:
                    pass
        con = sqlite3.connect(snap)
        self.parent = {}    # itemid -> (dir_itemid, nfc(name))
        self.hashes = {}    # (dir_itemid, nfc(name)) -> sha256 hex
        self._byname = defaultdict(list)  # nfc(name) -> [itemid] (dirs and files)
        for itemid, dirid, fn, md in con.execute(
                "SELECT itemid, dir_itemid, CAST(filename AS TEXT), metadata "
                "FROM fp_local_tree WHERE latest"):
            if fn is None:
                continue
            fn = nfc(fn)
            self.parent[itemid] = (dirid, fn)
            self._byname[fn].append(itemid)
            if md:
                top = _fields(md)
                f4 = _fields(_first_msg(top, 4))
                f46 = _fields(_first_msg(f4, 6))
                h = f46.get(1, [None])[0]
                if isinstance(h, (bytes, bytearray)) and len(h) == 32:
                    self.hashes[(dirid, fn)] = h.hex()
        con.close()
        self._dircache = {}

    def resolve_dir(self, abspath):
        """Map an absolute folder path to its itemid by reconstructing full
        paths for every same-named candidate. Returns None if not found."""
        ap = os.path.realpath(abspath)
        if ap in self._dircache:
            return self._dircache[ap]
        rel = os.path.relpath(ap, self.root)
        comps = [] if rel == "." else [nfc(c) for c in rel.split(os.sep)]
        if rel.startswith("..") or not comps:
            # `..` = outside the Dropbox root; `.` = the root itself (no
            # component to match). Cloud files sitting directly in the root are
            # rare and simply fall through to a local read if materialized.
            self._dircache[ap] = None
            return None
        found = None
        for cand in self._byname.get(comps[-1], []):
            parts = []; cur = cand; seen = set()
            while cur in self.parent and cur not in seen:
                seen.add(cur)
                d, f = self.parent[cur]
                parts.append(f); cur = d
            parts.reverse()
            # Drop empty leading components: the Dropbox account-root node is
            # stored with an empty name, so a real path reconstructs as
            # ['', 'Folder', ...]. Comparing against the on-disk components
            # (which have no such empty root) requires stripping it.
            parts = [p for p in parts if p]
            if parts == comps:
                found = cand
                break
        self._dircache[ap] = found
        return found

    def hash_for(self, dir_abspath, filename):
        dirid = self.resolve_dir(dir_abspath)
        if dirid is None:
            return None
        return self.hashes.get((dirid, nfc(filename)))

def dropbox_root_for(path):
    """Return the Dropbox root that contains `path`, or None."""
    rp = os.path.realpath(path)
    for root in _dropbox_roots():
        if rp == root or rp.startswith(root + os.sep):
            return root
    return None

def make_dbx(root, workdir):
    """Build a DropboxDB for `root`, or None (not a Dropbox folder, DB missing,
    or DB unreadable). Never raises: a bad DB degrades to local-only coverage."""
    dbx_root = dropbox_root_for(root)
    if not dbx_root:
        return None
    if not os.path.exists(NUCLEUS):
        out("  WARNING: Dropbox folder detected, but its sync database "
            "(nucleus.sqlite3) is not present (Dropbox not installed/running for "
            "this account layout) — proceeding with locally-present files only; "
            "cloud-only files will be skipped as not-locally-present.")
        return None
    out("  (Dropbox folder detected — cloud-only files identified via Dropbox's "
        "own hash database, no downloads)")
    try:
        return DropboxDB(workdir, dbx_root)
    except (sqlite3.Error, OSError) as e:
        out(f"  WARNING: could not read Dropbox's database ({e.__class__.__name__}) "
            f"— proceeding with locally-present files only.")
        return None

# ---------------- identity --------------------------------------------------
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()

HASHCACHE_PATH = os.path.expanduser("~/.cache/check-for-dupes/hashcache.sqlite3")

class HashCache:
    """Optional on-disk hash cache, keyed by (absolute path, size, mtime_ns),
    stored OUTSIDE any target tree so it can never be swept up as a duplicate.

    The key includes both size and nanosecond mtime, so ANY change to a file
    (which updates its mtime) yields a different key and therefore a cache MISS —
    a cached hash is only ever returned for a file that is byte-for-byte the same
    as when it was hashed. It caches ONLY hashes we computed ourselves from real
    local bytes; Dropbox's cloud-DB hashes are never stored here. Every operation
    is best-effort: any error degrades to "no cache", never to a wrong hash.

    Used to speed up `scan`/`move` re-runs. `verify` deliberately does NOT consult
    it — that pass always re-hashes local bytes so its no-data-lost proof rests on
    a fresh read, honoring the 'locally-present bytes are always re-hashed'
    invariant."""

    def __init__(self, path=HASHCACHE_PATH):
        self.con = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # Bound the cache: rows for edited/renamed/removed files are never
            # evicted, so past ~256 MB just rebuild from scratch — it is only
            # a cache, and a fresh one rebuilds itself over the next runs.
            if os.path.exists(path) and os.path.getsize(path) > 256 * 1024 * 1024:
                os.remove(path)
            self.con = sqlite3.connect(path)
            self.con.execute(
                "CREATE TABLE IF NOT EXISTS hashes "
                "(path TEXT, size INTEGER, mtime_ns INTEGER, sha256 TEXT, "
                " PRIMARY KEY (path, size, mtime_ns))")
            self.con.commit()
        except (sqlite3.Error, OSError):
            if self.con is not None:
                try:
                    self.con.close()
                except sqlite3.Error:
                    pass
            self.con = None

    def get(self, key):
        if self.con is None:
            return None
        p, s, m = key
        try:
            row = self.con.execute(
                "SELECT sha256 FROM hashes WHERE path=? AND size=? AND mtime_ns=?",
                (p, s, m)).fetchone()
        except (sqlite3.Error, ValueError):
            # ValueError covers UnicodeEncodeError from surrogate-escaped paths
            # (non-UTF-8 names on SMB/exFAT mounts) — degrade to a cache miss.
            return None
        return row[0] if row else None

    def put(self, key, sha):
        if self.con is None:
            return
        p, s, m = key
        try:
            self.con.execute(
                "INSERT OR REPLACE INTO hashes (path, size, mtime_ns, sha256) "
                "VALUES (?, ?, ?, ?)", (p, s, m, sha))
            self.con.commit()
        except (sqlite3.Error, ValueError):
            # ValueError covers UnicodeEncodeError from surrogate-escaped paths
            pass

    def close(self):
        if self.con is not None:
            try:
                self.con.close()
            except sqlite3.Error:
                pass
            self.con = None

class Scanner:
    def __init__(self, root, dbx=None, quiet=False, recent_secs=DEFAULT_RECENT_SECS,
                 hash_cache=None, label="", persistent=None, tick_cb=None):
        self.root = os.path.realpath(root)
        self.dbx = dbx
        self.quiet = quiet
        self.recent_secs = recent_secs
        self.cache = hash_cache if hash_cache is not None else {}
        self.persistent = persistent   # optional HashCache (scan/move only)
        self.tick_cb = tick_cb         # e.g. Lock.touch — keeps a lock fresh
                                       # DURING a multi-hour hashing pass, not
                                       # just at pass boundaries
        self.label = label
        self.hashed = 0
        self.bytes_hashed = 0
        self.cloud = 0
        self._last_progress = time.time()
        self._last_cb = time.time()

    def _tick(self):
        now = time.time()
        if self.tick_cb is not None and now - self._last_cb >= 60:
            self._last_cb = now
            self.tick_cb()
        if self.quiet:
            return
        if now - self._last_progress >= PROGRESS_EVERY:
            self._last_progress = now
            lbl = f"[{self.label}] " if self.label else ""
            print(f"    {lbl}hashed {self.hashed} files / {human(self.bytes_hashed)}"
                  f" · {self.cloud} cloud lookups", file=sys.stderr)

    def identity(self, absdir, name, st):
        """Return (sha256, source) or (None, skip_reason)."""
        p = os.path.join(absdir, name)
        if st.st_size == 0:
            return None, "empty"
        # Guard against future mtimes: a negative age must NOT read as "recent"
        # forever (that would permanently skip the file).
        age = time.time() - st.st_mtime
        if 0 <= age < self.recent_secs:
            return None, "recently-modified"
        if st_is_local(st, p):
            key = (p, st.st_size, getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
            h = self.cache.get(key)
            if h is None and self.persistent is not None:
                h = self.persistent.get(key)
                if h is not None:
                    self.cache[key] = h
            if h is None:
                try:
                    h = sha256_file(p)
                except OSError:
                    return None, "read-error"
                self.cache[key] = h
                if self.persistent is not None:
                    self.persistent.put(key, h)
                self.hashed += 1
                self.bytes_hashed += st.st_size
                self._tick()
            return h, "real-sha256"
        # cloud-only placeholder: never read (would trigger a download)
        if self.dbx:
            h = self.dbx.hash_for(absdir, name)
            self.cloud += 1
            self._tick()
            if h:
                return h, "cloud-db"
        return None, "not-locally-present"

    def _prune(self, dirpath, dirnames):
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".")
            and not is_dup_dirname(d)
            and not is_bundle(d)
            and not os.path.islink(os.path.join(dirpath, d)))

    def collect(self):
        """Walk the tree (skipping Duplicates dirs, hidden dirs, bundles, and
        symlinks). Returns (entries, skips, skip_entries)."""
        entries = []; skips = Counter(); skip_names = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            self._prune(dirpath, dirnames)
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                p = os.path.join(dirpath, name)
                if os.path.islink(p):
                    skips["symlink"] += 1
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    skips["stat-error"] += 1
                    continue
                h, src = self.identity(dirpath, name, st)
                if h is None:
                    skips[src] += 1
                    skip_names.append({"rel": os.path.relpath(p, self.root),
                                       "reason": src, "size": st.st_size})
                    continue
                entries.append({
                    "rel": os.path.relpath(p, self.root),
                    "dir_rel": os.path.relpath(dirpath, self.root),
                    "name": name, "size": st.st_size,
                    "hash": h, "source": src,
                })
        return entries, skips, skip_names

# ---------------- keeper ranking & planning ---------------------------------
# Copy-suffix detector. Extension allows up to two dot-segments so compound
# extensions like ".tar.gz" are recognised (otherwise a "(1)" copy could win
# the keeper contest over a clean name).
COPY_RE = re.compile(
    r"^(.*?) (?:\((\d+)\)|copy(?: (\d+))?)((?:\.[^.\s]+){0,2})$", re.I)

def strip_copy(name):
    m = COPY_RE.match(name)
    return (m.group(1) + (m.group(4) or "")) if m else name

def rank(entry):
    """Lower sorts first = better keeper. Prefer: clean name (no copy suffix),
    then lowest copy number, then shallower path, then shortest name."""
    n = entry["name"]
    m = COPY_RE.match(n)
    if m:
        cls, idx = 1, int(m.group(2) or m.group(3) or 1)
    else:
        cls, idx = 0, 0
    depth = entry["rel"].count(os.sep)
    return (cls, idx, depth, len(n), entry["rel"])

def poisoned_hashes(items):
    """A valid SHA-256 uniquely determines a file's size, so a hash observed at
    MORE THAN ONE distinct size cannot be a real content hash — it is a
    placeholder/sentinel or corrupt record (real case: Dropbox assigning one
    sentinel SHA-256 to ~89 different cloud-only files of 18 different sizes).
    Such a hash must never drive keeper-matching, or unrelated files get
    quarantined against a bogus 'twin'. Returns the set of poisoned hashes.

    `items` is any iterable of dicts carrying "hash" and "size". This is a
    global check on purpose: the size↔hash law holds across the whole corpus,
    independent of folder or scope."""
    sizes = defaultdict(set)
    for it in items:
        h = it.get("hash")
        if h is not None:
            sizes[h].add(it["size"])
    return {h for h, s in sizes.items() if len(s) > 1}

def build_plan(entries, root, scope):
    """Group by content; per group keep the best-ranked, move the rest.
    Returns (plan, poisoned): `plan` is a list of {entry, keeper, dup_dir};
    `poisoned` is the entries excluded because their content hash is a
    placeholder/sentinel (one hash seen at multiple sizes). Poisoned files are
    NEVER quarantined — their identity is unprovable without a real re-hash, so
    they are left in place and reported."""
    poison = poisoned_hashes(entries)
    poisoned = [e for e in entries if e["hash"] in poison]
    groups = defaultdict(list)
    for e in entries:
        if e["hash"] in poison:
            continue
        key = e["hash"] if scope == "single" else (e["dir_rel"], e["hash"])
        groups[key].append(e)
    plan = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=rank)
        keeper = members[0]
        if scope == "single":
            dup_dir = os.path.join(root, DUP_NAME)
        else:
            dd = keeper["dir_rel"]
            dup_dir = os.path.join(root, "" if dd == "." else dd, DUP_NAME)
        for m in members[1:]:
            plan.append({"entry": m, "keeper": keeper, "dup_dir": dup_dir})
    return plan, poisoned

def free_dest(dup_dir, name):
    """Collision-safe destination name inside dup_dir. Never overwrites, and
    never lands on one of our reserved support-file names."""
    def taken(n):
        return n.lower() in RESERVED or os.path.exists(os.path.join(dup_dir, n))
    if not taken(name):
        return name
    stem, ext = os.path.splitext(name)
    i = 2
    while taken(f"{stem} [dup{i}]{ext}"):
        i += 1
    return f"{stem} [dup{i}]{ext}"

def safe_dup_dir(dup_dir, root):
    """Refuse a Duplicates path that is a symlink or resolves outside root — a
    planted symlink must not send quarantined files out of the tree."""
    if os.path.islink(dup_dir):
        return False
    parent = os.path.dirname(dup_dir)
    rp = os.path.realpath(dup_dir)
    rroot = os.path.realpath(root)
    if os.path.exists(parent) and os.path.islink(parent):
        return False
    return rp == rroot or rp.startswith(rroot + os.sep)

# ---------------- artifacts (manifest / undo / readme) ----------------------
ENGINE_PATH = os.path.realpath(__file__)

# The generated undo script prefers to hand off to this engine (one source of
# truth for restore — including the csv_safe round-trip), and only falls back to
# a self-contained restorer if the engine is not installed at the recorded path.
# The fallback undoes the same defusing so files whose names start with = + - @
# restore correctly, and blocks nothing silently.
UNDO_SCRIPT = r'''#!/bin/bash
# Restore every quarantined file back to where it came from.
cd "$(dirname "$0")" || exit 1
ENGINE="__ENGINE_PATH__"
PARENT="$(cd .. && pwd)"
if [ -f "$ENGINE" ]; then
    exec python3 "$ENGINE" undo "$PARENT"
fi
# Fallback: engine not found — restore directly from this manifest.
python3 - <<'PYEOF'
import csv, os
base = os.path.realpath("..")            # the folder this Duplicates dir sits in
here = os.path.realpath(".")
_TRIG = ("=", "+", "-", "@", "\t", "\r", "'")
def unsafe(v):
    # inverse of the manifest's CSV-injection defusing (a leading quote inserted
    # before a trigger char, quotes themselves doubled) — MUST match the
    # engine's csv_unsafe exactly or defused names restore under wrong names
    return v[1:] if len(v) >= 2 and v[0] == "'" and v[1] in _TRIG else v
def resolve(name):
    # de-defused name FIRST (exact for current manifests); raw only as a
    # legacy fallback — mirrors the engine's _resolve_stored order
    alt = unsafe(name)
    if alt != name and os.path.exists(os.path.join(here, alt)):
        return os.path.join(here, alt), True
    raw = os.path.join(here, name)
    if os.path.exists(raw):
        return raw, True
    return os.path.join(here, alt), False
n = s = bad = 0
try:
    fh = open("_manifest.csv", newline="")
except OSError:
    print("could not read _manifest.csv"); raise SystemExit(1)
with fh:
    for row in csv.DictReader(fh):
        orig = unsafe(row["original_relpath"])
        src, exists = resolve(row["stored_as"])
        dst = os.path.join(base, orig)
        # containment: never restore outside the tree, never read outside here
        if os.path.isabs(orig) or os.path.realpath(dst) != os.path.normpath(os.path.join(base, orig)) \
           or not (os.path.realpath(dst) == base or os.path.realpath(dst).startswith(base + os.sep)):
            print("REFUSED (escapes folder):", orig); bad += 1; continue
        rp_src = os.path.realpath(src)
        if not (rp_src == here or rp_src.startswith(here + os.sep)):
            print("REFUSED (source escapes):", row["stored_as"]); bad += 1; continue
        if not exists:
            if not os.path.exists(dst):
                print("UNACCOUNTED (investigate!):", orig); bad += 1
            continue
        if os.path.exists(dst):
            print("SKIP (exists):", orig); s += 1; continue
        try:
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            os.rename(src, dst); n += 1
        except OSError as e:
            # one failing row must not abort the whole restore
            print("FAILED:", orig, "-", e.__class__.__name__); bad += 1
print("restored", n, "files, skipped", s, "refused/failed", bad)
PYEOF
'''

def write_support_files(dup_dir):
    us = os.path.join(dup_dir, UNDO_SH)
    if not os.path.exists(us):
        with open(us, "w") as f:
            f.write(UNDO_SCRIPT.replace("__ENGINE_PATH__", ENGINE_PATH))
        os.chmod(us, 0o755)
    rd = os.path.join(dup_dir, README)
    if not os.path.exists(rd):
        with open(rd, "w") as f:
            f.write(f"""DUPLICATE FILES — quarantined by check-for-dupes v{__version__}
{'=' * 60}
Every file in this folder is a byte-for-byte duplicate of a file that
is still in place outside this folder. NOTHING WAS DELETED.

Duplicates were identified by SHA-256 content hash — never by filename.
Files that merely share a name but hold different content were NOT touched.

  {MANIFEST}   what moved, what original it duplicates, its hash, the scope
  {UNDO_SH}    run this to move everything back (refuses paths outside the tree)

Deleting this folder is what actually reclaims the space.
""")

MANIFEST_HEADER = ["stored_as", "original_relpath", "keeper_relpath",
                   "sha256", "size_bytes", "identity_source", "scope", "moved_at"]

def append_manifest(dup_dir, rows):
    """Append rows durably (flush+fsync) so an interrupt can't strand files
    with no record. Values are CSV-injection-defused."""
    mp = os.path.join(dup_dir, MANIFEST)
    new = not os.path.exists(mp)
    with open(mp, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(MANIFEST_HEADER)
        for r in rows:
            w.writerow([csv_safe(x) for x in r])
        f.flush()
        os.fsync(f.fileno())

def read_manifest_scope(dup_dir):
    """Return the scope recorded in a Duplicates dir's manifest, or None."""
    mp = os.path.join(dup_dir, MANIFEST)
    if not os.path.exists(mp):
        return None
    try:
        with open(mp, newline="") as f:
            for row in csv.DictReader(f):
                return row.get("scope") or None
    except OSError:
        return None
    return None

# ---------------- Duplicates-dir discovery (shared by verify/undo) ----------
def find_dup_dirs(root, managed_only=False):
    """Yield every Duplicates directory under root, pruning hidden dirs,
    bundles and symlinks. If managed_only, only dirs that contain OUR manifest
    (so a user's unrelated 'duplicates' folder is never conscripted)."""
    dup_dirs = []
    for dirpath, dirnames, _ in os.walk(root):
        for d in list(dirnames):
            full = os.path.join(dirpath, d)
            if is_dup_dirname(d):
                dirnames.remove(d)
                if os.path.islink(full):
                    continue
                if managed_only and not os.path.exists(os.path.join(full, MANIFEST)):
                    continue
                dup_dirs.append(full)
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and not is_bundle(d)
                       and not os.path.islink(os.path.join(dirpath, d))]
    return dup_dirs

# ---------------- guardrails ------------------------------------------------
def _fold(p):
    """Case-fold a path for guardrail comparison. os.path.normcase is a no-op
    on POSIX, but macOS's default APFS is case-insensitive, so a case variant
    (~/library, /users/…) reaches the same physical folder — compare folded.
    Wrongly refusing a rare case-sensitive twin is the safe direction."""
    return os.path.normcase(p).lower()

def check_target(root):
    ap = os.path.realpath(root)
    apn = _fold(ap)
    home = os.path.realpath(os.path.expanduser("~"))
    protected = ["/", home]
    for sub in ("Library", "Desktop", "Documents", "Downloads", "Pictures",
                "Movies", "Music", "Public"):
        protected.append(os.path.join(home, sub))
    # The whole iCloud Drive root is as broad and cloud-backed as the Dropbox
    # root, so refuse it too — point at a specific folder inside it instead.
    protected.append(os.path.join(home, "Library", "Mobile Documents",
                                  "com~apple~CloudDocs"))
    protected.extend(_dropbox_roots())
    protected_n = {_fold(p) for p in protected}

    def refuse(msg):
        print(f"REFUSED: {safe(msg)}", file=sys.stderr)
        sys.exit(3)

    if not os.path.isdir(ap):
        refuse(f"'{ap}' is not a directory.")
    if os.path.basename(apn) == DUP_NAME.lower():
        refuse(f"'{ap}' is itself a Duplicates folder — point at the parent.")
    if apn in protected_n or (os.sep == "/" and ap.count("/") <= 1):
        refuse(f"'{ap}' is too broad/system-critical to dedupe. "
               f"Point at a specific content folder.")
    # a whole external volume root: /Volumes/<Name>
    if os.path.dirname(apn) == "/volumes":
        refuse(f"'{ap}' is an entire external volume — point at a folder on it.")
    return ap

def _pid_alive(pid):
    """True if a process with this PID currently exists. os.kill(pid, 0) sends no
    signal; it just probes existence. PermissionError means it exists but is
    owned by someone else (still alive)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True

def _lock_is_stale(path):
    """A lock is reclaimable if its recorded owner PID is gone. When the owner
    is known and ALIVE it is never reclaimed regardless of mtime (a legitimately
    long run must not be stolen). Only when the owner is unknown (unreadable/
    corrupt lock, or a lock written by ANOTHER machine syncing this folder —
    its PID is meaningless in our process table) does the mtime age decide; a
    live run touches its lock every pass, so a stale mtime then implies
    dead/hung."""
    pid = None; host = None
    try:
        with open(path) as f:
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
        age = time.time() - os.path.getmtime(path)
    except OSError:
        age = 0
    return age > STALE_LOCK_SECS

def _live_lock_at(dirpath):
    """True if dirpath holds a .dedupe.lock belonging to a LIVE run."""
    lp = os.path.join(dirpath, LOCK)
    return os.path.exists(lp) and not _lock_is_stale(lp)

def _foreign_lock_between(root, dirpath):
    """True if any folder from dirpath up to (but excluding) root holds a live
    lock — i.e. another run is mutating that subtree right now."""
    rroot = os.path.realpath(root); cur = os.path.realpath(dirpath)
    while cur != rroot and len(cur) > len(rroot):
        if _live_lock_at(cur):
            return True
        cur = os.path.dirname(cur)
    return False

class Lock:
    """Best-effort single-writer lock for mutating runs (`move`/`heal`/`undo`),
    so two concurrent runs can't race on the same Duplicates folder.

    A held lock is kept fresh (its mtime is touched every pass), so a lock whose
    mtime has gone stale (> STALE_LOCK_SECS) belongs to a run that died or hung.
    Before reclaiming, we also check the PID recorded inside the lock: if that
    process is gone the lock is abandoned and reclaimed immediately; if it is
    still alive we never reclaim (a legitimately long run must not be stolen)."""
    def __init__(self, root):
        self.root = root
        self.path = os.path.join(root, LOCK)
        self.held = False

    def _is_stale(self):
        return _lock_is_stale(self.path)

    def _refuse(self, why):
        print(f"REFUSED: {why} (lock: {safe(self.path)}).", file=sys.stderr)
        sys.exit(3)

    def __enter__(self):
        for _ in (1, 2):        # second try only after reclaiming a stale lock
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.write(fd, f"{os.getpid()} {int(time.time())} "
                             f"{socket.gethostname()}\n".encode())
                os.close(fd)
                self.held = True
                # A live mutating run on an ENCLOSING folder covers this tree
                # too (e.g. `undo` on a subfolder while `move` runs on the
                # parent) — their lock files are disjoint, so check ancestors.
                cur = os.path.dirname(os.path.realpath(self.root))
                while True:
                    if _live_lock_at(cur):
                        self.__exit__()
                        self._refuse(f"a dedupe run is active on the enclosing "
                                     f"folder {safe(cur)} — wait for it to finish")
                    nxt = os.path.dirname(cur)
                    if nxt == cur:
                        break
                    cur = nxt
                return self
            except FileExistsError:
                pass
            except OSError as e:    # target vanished, no space, permissions…
                self._refuse(f"cannot create the lock file ({e.__class__.__name__})")
            if not self._is_stale():
                self._refuse("another dedupe run is active on this folder — "
                             "wait for it to finish")
            # Reclaim atomically: only the ONE waiter that wins the .reclaim
            # guard may delete the stale lock — otherwise a slower waiter could
            # delete the winner's freshly created lock and both would proceed.
            guard = self.path + ".reclaim"
            try:
                gfd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.close(gfd)
            except FileExistsError:
                try:
                    gage = time.time() - os.path.getmtime(guard)
                except OSError:
                    gage = 0
                if gage > 60:    # a reclaimer died mid-reclaim: clear its guard
                    try:
                        os.remove(guard)
                    except OSError:
                        pass
                self._refuse("another run is already reclaiming this folder's "
                             "stale lock — retry in a moment")
            except OSError as e:
                self._refuse(f"cannot create the reclaim guard ({e.__class__.__name__})")
            try:
                os.remove(self.path)
            except OSError:
                pass
            finally:
                try:
                    os.remove(guard)
                except OSError:
                    pass
        self._refuse("could not acquire the lock after reclaiming a stale one")

    def touch(self):
        """Refresh the lock's mtime so a long run never looks abandoned."""
        if self.held:
            try:
                os.utime(self.path, None)
            except OSError:
                pass

    def __exit__(self, *exc):
        if not self.held:
            return
        # Only remove a lock we still own — if ours was wrongly reclaimed,
        # deleting the new holder's lock would cascade the race.
        try:
            with open(self.path) as f:
                owner = int(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            owner = os.getpid()   # unreadable: assume ours, best-effort clean
        if owner == os.getpid():
            try:
                os.remove(self.path)
            except OSError:
                pass

# ---------------- subcommands -----------------------------------------------
def _scan(root, scope, recent_secs, quiet, hash_cache=None, label="", persistent=None,
          tick_cb=None):
    with tempfile.TemporaryDirectory() as wd:
        dbx = make_dbx(root, wd)
        sc = Scanner(root, dbx, quiet=quiet, recent_secs=recent_secs, tick_cb=tick_cb,
                     hash_cache=hash_cache, label=label, persistent=persistent)
        entries, skips, skip_names = sc.collect()
    return entries, skips, skip_names

def cmd_scan(args):
    root = check_target(args.folder)
    persistent = HashCache()
    try:
        entries, skips, skip_names = _scan(root, args.scope, args.recent_secs,
                                           args.quiet, persistent=persistent)
    finally:
        persistent.close()
    plan, poisoned = build_plan(entries, root, args.scope)
    keepers = {id(p["keeper"]) for p in plan}
    total = sum(p["entry"]["size"] for p in plan)
    fam = defaultdict(set)
    for e in entries:
        fam[(e["dir_rel"], strip_copy(e["name"]))].add(e["hash"])
    traps = sum(1 for v in fam.values() if len(v) > 1)
    result = {
        "action": "scan", "scope": args.scope, "version": __version__,
        "files_scanned": len(entries) + sum(skips.values()),
        "files_identified": len(entries),
        "skipped": dict(skips),
        "duplicate_groups": len(keepers),
        "files_to_move": len(plan),
        "bytes_to_move": total,
        "same_name_different_content_families": traps,
        "unique_contents": len({e["hash"] for e in entries}),
        "placeholder_hash_files": len(poisoned),
        "placeholder_hashes": sorted({e["hash"] for e in poisoned}),
    }
    out(f"\nSCAN of {safe(root)}  (scope: {args.scope})")
    out(f"  files identified          : {result['files_identified']}"
        f"  (skipped: {dict(skips) or 'none'})")
    out(f"  unique contents           : {result['unique_contents']}")
    out(f"  duplicate groups          : {len(keepers)}")
    out(f"  redundant copies to move  : {len(plan)}  ({human(total)})")
    out(f"  same-name/diff-content families protected: {traps}")
    if poisoned:
        out(f"  UNVERIFIABLE placeholder-hash files (LEFT IN PLACE): {len(poisoned)}"
            f"  in {len(result['placeholder_hashes'])} bogus hash group(s)")
        out( "     Dropbox reported one content hash for several different file")
        out( "     sizes — that identity is bogus, so these are NOT deduplicated.")
    if args.show and plan:
        out("\n  sample moves:")
        for p in plan[:args.show]:
            out(f"    {safe(p['entry']['rel'])}  ->  {DUP_NAME}/   "
                f"(dup of {safe(p['keeper']['rel'])})")
    emit_json(result)
    return 0

def cmd_move(args):
    if getattr(args, "dry_run", False):
        return cmd_scan(args)
    root = check_target(args.folder)
    hash_cache = {}          # (path,size,mtime_ns) -> sha256, shared across passes
    persistent = HashCache()
    total_moved = 0; total_bytes = 0; failed = 0; passes = 0
    all_dup_dirs = set(); converged = False; interrupted = False
    poisoned = []            # placeholder-hash files (never moved); last pass wins
    pending = defaultdict(list)   # dup_dir -> rows not yet flushed

    def flush():
        for dup_dir, rows in list(pending.items()):
            if not rows:
                continue
            if safe_dup_dir(dup_dir, root):
                append_manifest(dup_dir, rows)
                write_support_files(dup_dir)
                pending[dup_dir].clear()
            else:
                # NEVER discard rows for files already moved: without their
                # manifest record they'd be invisible to undo/heal/verify.
                out(f"  WARNING: {safe(os.path.relpath(dup_dir, root))} became "
                    f"unsafe before its manifest flush — {len(rows)} row(s) "
                    f"held for retry")

    with Lock(root) as lock:
        try:
            for p in range(1, args.max_passes + 1):
                passes = p
                lock.touch()   # keep the lock fresh so a long run isn't reclaimed
                entries, skips, _ = _scan(root, args.scope, args.recent_secs,
                                          args.quiet, hash_cache, label=f"pass {p}",
                                          persistent=persistent, tick_cb=lock.touch)
                plan, poisoned = build_plan(entries, root, args.scope)
                if not plan:
                    converged = True
                    out(f"  pass {p}: nothing left to move"
                        f"  (skipped: {dict(skips) or 'none'})")
                    break
                moved = 0; guard_skipped = 0; pass_failed = 0
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                for item in plan:
                    e, k = item["entry"], item["keeper"]
                    src = os.path.join(root, e["rel"])
                    kp = os.path.join(root, k["rel"])
                    if not os.path.exists(src) or not os.path.exists(kp):
                        guard_skipped += 1
                        continue  # keeper must exist or we refuse to move
                    dup_dir = item["dup_dir"]
                    if not safe_dup_dir(dup_dir, root):
                        failed += 1; pass_failed += 1
                        out(f"  WARNING: refusing unsafe Duplicates path "
                            f"{safe(os.path.relpath(dup_dir, root))} (symlink/escape)")
                        continue
                    try:
                        os.makedirs(dup_dir, exist_ok=True)
                        stored = free_dest(dup_dir, e["name"])
                        os.rename(src, os.path.join(dup_dir, stored))
                    except OSError as err:
                        failed += 1; pass_failed += 1
                        out(f"  WARNING: could not move {safe(e['rel'])}: "
                            f"{err.__class__.__name__}")
                        continue
                    moved += 1; total_bytes += e["size"]
                    orig_rel = e["rel"] if args.scope == "single" else e["name"]
                    keep_rel = k["rel"] if args.scope == "single" else k["name"]
                    pending[dup_dir].append(
                        [stored, orig_rel, keep_rel, e["hash"], e["size"],
                         e["source"], args.scope, now])
                    all_dup_dirs.add(dup_dir)
                total_moved += moved
                flush()   # durable per-pass: an interrupt after this keeps the manifest
                out(f"  pass {p}: moved {moved}"
                    + (f", {pass_failed} failed" if pass_failed else "")
                    + (f", {guard_skipped} skipped (keeper/src vanished)"
                       if guard_skipped else "")
                    + f"  (skipped: {dict(skips) or 'none'})")
                if moved == 0:
                    # A non-empty plan that moved nothing means every item was
                    # blocked (keeper/src vanished, unsafe dup dir, rename failed).
                    # Retrying re-hashes the whole tree to reach the same wall, so
                    # stop now — but do NOT claim convergence: a plan that existed
                    # yet produced no moves still has outstanding work (guard-
                    # skipped or failed items), so the report and `verify` must
                    # flag it. Only a genuinely empty plan (handled above) is done.
                    converged = False
                    break
        except KeyboardInterrupt:
            interrupted = True
            out("\n  interrupted — flushing recovery manifest for files already moved...")
        finally:
            flush()
            persistent.close()

    result = {"action": "move", "scope": args.scope, "version": __version__,
              "moved": total_moved, "bytes_moved": total_bytes,
              "failed": failed, "passes": passes, "converged": converged,
              "interrupted": interrupted,
              "placeholder_hash_files": len(poisoned),
              "placeholder_hashes": sorted({e["hash"] for e in poisoned}),
              "duplicates_dirs": sorted(os.path.relpath(d, root) for d in all_dup_dirs)}
    out(f"\nMOVE complete: {total_moved} files ({human(total_bytes)}) "
        f"quarantined in {passes} pass(es); converged={converged}"
        + (f"; {failed} failed" if failed else ""))
    if poisoned:
        out(f"  NOTE: {len(poisoned)} file(s) with an invalid placeholder content "
            f"hash were LEFT IN PLACE (not quarantined) — Dropbox reported one hash "
            f"for several different file sizes, so their identity is unverifiable. "
            f"Materialize (download) them and re-run to dedupe safely.")
    if interrupted:
        out("  NOTE: interrupted — already-moved files have a valid manifest and "
            "are fully undoable. Re-run `move` to finish.")
    elif not converged:
        out("  NOTE: hit max-passes without converging — files may still be "
            "arriving (active import/sync). Re-run once the folder settles.")
    emit_json(result)
    return 0

def detect_scope(root):
    """Read the scope recorded in the tree's manifests; None if none/mixed."""
    seen = set()
    for dd in find_dup_dirs(root, managed_only=True):
        s = read_manifest_scope(dd)
        if s:
            seen.add(s)
    return seen.pop() if len(seen) == 1 else None

_TREE = ("\x00tree",)   # sentinel scope-key that can't collide with a real dir_rel

def scan_quarantine(root, scope, sc, dbx):
    """Hash the tree once and classify every quarantined file. Shared by
    `verify` (which reports) and `heal` (which acts). Returns a dict with:
      outside   : list of entries outside any Duplicates dir
      dup_dirs  : managed Duplicates dirs (they carry our manifest)
      unmanaged : relpaths of Duplicates dirs that are NOT ours (skipped)
      oskips    : Counter of why outside files were unidentifiable
      records   : per quarantined file — {dup_dir, name, path, restore_to,
                  hash, size, klass} with klass in
                  ok | orphan | inconclusive | poisoned | syncing | unreadable
      residual  : outside duplicate groups still present (>1 per scope key)
    An orphan is a quarantined file whose exact content exists NOWHERE outside
    (in its scope) AND whose size doesn't match any not-yet-indexed outside
    file (that softer case is 'inconclusive' — re-verify later, never healed)."""
    outside, oskips, oskip_entries = sc.collect()
    obs = defaultdict(set); sizes = defaultdict(set)
    for e in outside:
        obs[_TREE].add(e["hash"])
        obs[e["dir_rel"]].add(e["hash"])
    for s in oskip_entries:
        sizes[_TREE].add(s["size"])
        sizes[os.path.dirname(s["rel"]) or "."].add(s["size"])
    dup_dirs = find_dup_dirs(root, managed_only=True)
    unmanaged = [os.path.relpath(d, root)
                 for d in find_dup_dirs(root, managed_only=False)
                 if d not in dup_dirs]
    records = []
    for dd in dup_dirs:
        restore_to = os.path.dirname(dd)
        try:
            names = sorted(os.listdir(dd))
        except FileNotFoundError:
            # The Duplicates dir vanished between discovery and now (a live
            # cloud folder can churn under us): nothing left to check here.
            continue
        except OSError:
            # Still present but unreadable (permissions, I/O error): verify must
            # FAIL, not pass while this dir's contents went unchecked.
            records.append({"dup_dir": dd, "name": "", "path": dd,
                            "restore_to": restore_to, "size": 0,
                            "hash": None, "klass": "unreadable"})
            continue
        for name in names:
            if name.lower() in RESERVED or name.startswith("."):
                continue
            p = os.path.join(dd, name)
            try:
                if not os.path.isfile(p) or os.path.islink(p):
                    continue
                st = os.stat(p)
            except FileNotFoundError:
                continue          # file churned away mid-run; skip it
            except OSError:
                # EACCES/EIO is NOT churn: the file exists but cannot be
                # checked — it must fail verify, not vanish from every counter.
                records.append({"dup_dir": dd, "name": name, "path": p,
                                "restore_to": restore_to, "size": 0,
                                "hash": None, "klass": "unreadable"})
                continue
            if st.st_size == 0:
                continue
            rec = {"dup_dir": dd, "name": name, "path": p, "restore_to": restore_to,
                   "size": st.st_size, "hash": None, "klass": None}
            if st_is_local(st, p):
                try:
                    rec["hash"] = sha256_file(p)
                except OSError:
                    rec["klass"] = "unreadable"
            else:
                rec["hash"] = dbx.hash_for(dd, name) if dbx else None
                if not rec["hash"]:
                    rec["klass"] = "syncing"
            records.append(rec)
    # A valid content hash implies a unique size. Any hash seen at more than one
    # size across the whole tree (outside originals + quarantined copies) is a
    # placeholder/sentinel, so a quarantined file carrying it has NO provable
    # original — it is a hard failure ("poisoned"), never "ok". Computing this
    # over the UNION is what defeats the fooling: a 473 MB video and the 4.6 MB
    # JPG "keeper" sharing one sentinel hash expose it as multi-size here.
    poison = poisoned_hashes(outside + [r for r in records if r["hash"]])
    # An outside file whose hash is poisoned has an UNKNOWN true identity, so
    # its size must feed the inconclusive size-set: a quarantined file whose
    # only potential original went sentinel is "inconclusive" (re-verify later),
    # never a CONFIRMED orphan — heal would otherwise restore a copy right next
    # to its still-existing original.
    for e in outside:
        if e["hash"] in poison:
            sizes[_TREE].add(e["size"])
            sizes[e["dir_rel"]].add(e["size"])
    for rec in records:
        if rec["klass"]:                     # already terminal: unreadable / syncing
            continue
        if rec["hash"] in poison:
            rec["klass"] = "poisoned"
            continue
        if scope == "single":
            ok_set, size_set = obs[_TREE], sizes[_TREE]
        else:
            key = os.path.relpath(os.path.dirname(rec["dup_dir"]), root)
            ok_set, size_set = obs.get(key, set()), sizes.get(key, set())
        if rec["hash"] in ok_set:
            rec["klass"] = "ok"
        elif rec["size"] in size_set:
            rec["klass"] = "inconclusive"
        else:
            rec["klass"] = "orphan"
    # Residual duplicates = real content still sitting outside Duplicates. Files
    # carrying a POISONED hash (one hash reported at multiple sizes — the Dropbox
    # sentinel case) are the very files scan/move deliberately refused to touch
    # because their identity is unprovable; grouping them here would raise a false
    # "residual duplicates" alarm and fail verify. Exclude them from the grouping
    # and surface them under a neutral counter that does NOT fail the run.
    groups = defaultdict(list)
    outside_placeholder = 0
    for e in outside:
        if e["hash"] in poison:
            outside_placeholder += 1
            continue
        key = e["hash"] if scope == "single" else (e["dir_rel"], e["hash"])
        groups[key].append(e["rel"])
    residual = {k: v for k, v in groups.items() if len(v) > 1}
    return {"outside": outside, "dup_dirs": dup_dirs, "unmanaged": unmanaged,
            "oskips": oskips, "records": records, "residual": residual,
            "outside_placeholder_hash_files": outside_placeholder}

def cmd_verify(args):
    root = check_target(args.folder)
    scope = args.scope
    if scope is None:                    # not given: honor what `move` recorded
        scope = detect_scope(root) or "single"
    problems = []
    with tempfile.TemporaryDirectory() as wd:
        dbx = make_dbx(root, wd)
        sc = Scanner(root, dbx, quiet=args.quiet, recent_secs=args.recent_secs,
                     label="verify")
        q = scan_quarantine(root, scope, sc, dbx)
        oskips, dup_dirs, unmanaged, residual = (q["oskips"], q["dup_dirs"],
                                                 q["unmanaged"], q["residual"])
        outside_placeholder = q["outside_placeholder_hash_files"]
        recs = q["records"]
        checked = sum(1 for r in recs if r["klass"] in
                      ("ok", "orphan", "inconclusive", "poisoned"))
        orphans = sum(1 for r in recs if r["klass"] == "orphan")
        inconclusive = sum(1 for r in recs if r["klass"] == "inconclusive")
        syncing = sum(1 for r in recs if r["klass"] == "syncing")
        unreadable = sum(1 for r in recs if r["klass"] == "unreadable")
        poisoned = sum(1 for r in recs if r["klass"] == "poisoned")
        orphan_list = [os.path.relpath(r["path"], root) for r in recs if r["klass"] == "orphan"]
        orphan_list += [os.path.relpath(r["path"], root) + " (UNREADABLE)"
                        for r in recs if r["klass"] == "unreadable"]
        inconclusive_list = [os.path.relpath(r["path"], root)
                             for r in recs if r["klass"] == "inconclusive"]
        poisoned_list = [os.path.relpath(r["path"], root)
                         for r in recs if r["klass"] == "poisoned"]
    if orphans:
        problems.append(f"{orphans} quarantined file(s) have NO identical "
                        f"original outside Duplicates")
    if poisoned:
        problems.append(f"{poisoned} quarantined file(s) carry an INVALID placeholder "
                        f"content hash (one hash reported at multiple file sizes) — "
                        f"their identity is UNVERIFIABLE; do NOT delete Duplicates")
    if unreadable:
        problems.append(f"{unreadable} quarantined file(s) are present but UNREADABLE")
    if residual:
        problems.append(f"{len(residual)} duplicate group(s) still present "
                        f"outside Duplicates")
    needs_reverify = inconclusive > 0 or syncing > 0
    result = {"action": "verify", "scope": scope, "version": __version__,
              "duplicates_dirs": len(dup_dirs),
              "unmanaged_duplicates_dirs": unmanaged,
              "quarantined_checked": checked,
              "confirmed_orphans": orphans, "orphan_files": orphan_list[:50],
              "poisoned": poisoned, "poisoned_files": poisoned_list[:50],
              "unreadable": unreadable,
              "inconclusive": inconclusive,
              "inconclusive_files": inconclusive_list[:20],
              "syncing": syncing,
              "residual_duplicate_groups": len(residual),
              "outside_placeholder_hash_files": outside_placeholder,
              "outside_skipped": dict(oskips),
              "needs_reverify": needs_reverify,
              "ok": not problems}
    out(f"\nVERIFY of {safe(root)}  (scope: {scope})")
    out(f"  Duplicates folders found        : {len(dup_dirs)}")
    if unmanaged:
        out(f"  unmanaged 'Duplicates' skipped  : {len(unmanaged)} (no manifest — not ours)")
    out(f"  quarantined files checked       : {checked}")
    out(f"  originals confirmed present     : {checked - orphans - inconclusive - poisoned}")
    out(f"  CONFIRMED ORPHANS (bad!)        : {orphans}")
    out(f"  INVALID placeholder-hash files (bad!) : {poisoned}")
    out(f"  unreadable quarantined files    : {unreadable}")
    out(f"  inconclusive (same-size unindexed original may exist — re-verify): {inconclusive}")
    out(f"  syncing (cloud hash not yet available) : {syncing}")
    out(f"  residual dupes outside          : {len(residual)}")
    if outside_placeholder:
        out(f"  outside placeholder-hash files (left in place, not a problem): "
            f"{outside_placeholder}")
    if dict(oskips):
        out(f"  outside files not identifiable  : {dict(oskips)}")
    for o in orphan_list[:20]:
        out(f"    ORPHAN: {safe(o)}")
    for pf in poisoned_list[:20]:
        out(f"    PLACEHOLDER-HASH (unverifiable): {safe(pf)}")
    for k, v in list(residual.items())[:10]:
        out(f"    residual group: {[safe(x) for x in sorted(v)]}")
    verdict = ("ALL CHECKS PASSED" if not problems and not needs_reverify
               else "PROBLEMS: " + "; ".join(problems) if problems
               else "NO PROBLEMS PROVEN, but cloud records are lagging — "
                    "re-run verify once syncing settles (do NOT delete Duplicates yet)")
    out(f"\n  {verdict}")
    emit_json(result)
    return 0 if not problems else 2

def _restore_rank(name):
    """Pick the cleanest copy to bring back: fewest ` [dupN]` collision tags,
    then lowest copy number, then shortest name."""
    tags = name.count(" [dup")
    m = COPY_RE.match(name)
    idx = int(m.group(2) or m.group(3) or 1) if m else 0
    return (tags, idx, len(name), name)

def _restore_name(dest, stored):
    """A clean, collision-free name in `dest` for a recovered file: strip the
    trailing ` [dupN]` tag we may have added, then avoid any real collision."""
    stem, ext = os.path.splitext(stored)
    stem = re.sub(r" \[dup\d+\]$", "", stem)
    cand = stem + ext
    if cand.lower() not in RESERVED and not os.path.exists(os.path.join(dest, cand)):
        return cand
    i = 2
    while os.path.exists(os.path.join(dest, f"{stem} [recovered{i}]{ext}")):
        i += 1
    return f"{stem} [recovered{i}]{ext}"

def cmd_heal(args):
    """Recover files the quarantine holds that have NO copy left outside it —
    the mistakes an older/foreign pass made (e.g. stale-hash mis-filing). For
    each distinct orphaned content, restore ONE copy to where it belongs and
    leave its true duplicates behind. Never deletes; safe to re-run."""
    root = check_target(args.folder)
    scope = args.scope
    if scope is None:
        scope = detect_scope(root) or "single"
    dry = getattr(args, "dry_run", False)
    # A real (non-dry) heal renames files, so it must not race a concurrent
    # `move`/`undo` — take the same single-writer lock. A dry-run only reads.
    lock = Lock(root) if not dry else nullcontext()
    with lock, tempfile.TemporaryDirectory() as wd:
        dbx = make_dbx(root, wd)
        sc = Scanner(root, dbx, quiet=args.quiet, recent_secs=args.recent_secs,
                     label="heal", tick_cb=(lock.touch if not dry else None))
        q = scan_quarantine(root, scope, sc, dbx)
        orphan_recs = [r for r in q["records"] if r["klass"] == "orphan"]
        inconclusive = sum(1 for r in q["records"] if r["klass"] == "inconclusive")
        syncing = sum(1 for r in q["records"] if r["klass"] == "syncing")
        poisoned = sum(1 for r in q["records"] if r["klass"] == "poisoned")
        # group orphans by (where they'd be restored, content) so we bring back
        # exactly one copy per unique content and leave the rest quarantined.
        groups = defaultdict(list)
        for r in orphan_recs:
            groups[(r["restore_to"], r["hash"])].append(r)
        restored = []; failed = 0
        for (dest, h), members in sorted(groups.items(), key=lambda kv: kv[1][0]["name"]):
            best = sorted(members, key=lambda r: _restore_rank(r["name"]))[0]
            newname = _restore_name(dest, best["name"])
            dst = os.path.join(dest, newname)
            row = {"restored_from": os.path.relpath(best["path"], root),
                   "restored_to": os.path.relpath(dst, root),
                   "sha256": h, "size_bytes": best["size"],
                   "true_duplicates_left": len(members) - 1}
            if not dry:
                try:
                    os.rename(best["path"], dst)
                except OSError as e:
                    failed += 1
                    out(f"  could not restore {safe(best['name'])}: {e.__class__.__name__}")
                    continue
                # log the recovery alongside the quarantine it came from
                dd = best["dup_dir"]
                lp = os.path.join(dd, HEAL_LOG)
                newlog = not os.path.exists(lp)
                try:
                    with open(lp, "a", newline="") as f:
                        w = csv.writer(f)
                        if newlog:
                            w.writerow(["restored_from", "restored_to", "sha256",
                                        "size_bytes", "true_duplicates_left", "healed_at"])
                        w.writerow([csv_safe(row["restored_from"]), csv_safe(row["restored_to"]),
                                    h, best["size"], row["true_duplicates_left"],
                                    time.strftime("%Y-%m-%d %H:%M:%S")])
                        f.flush(); os.fsync(f.fileno())
                except OSError:
                    pass
            restored.append(row)
    bytes_r = sum(r["size_bytes"] for r in restored)
    result = {"action": "heal", "scope": scope, "version": __version__,
              "dry_run": dry, "recovered": len(restored),
              "bytes_recovered": bytes_r, "failed": failed,
              "true_duplicates_left_behind": sum(r["true_duplicates_left"] for r in restored),
              "still_inconclusive": inconclusive, "still_syncing": syncing,
              "still_poisoned": poisoned,
              "recovered_files": [r["restored_to"] for r in restored][:100]}
    verb = "WOULD RECOVER" if dry else "RECOVERED"
    out(f"\nHEAL of {safe(root)}  (scope: {scope}){'  [DRY RUN]' if dry else ''}")
    out(f"  unique files with no copy outside quarantine : {len(restored)}")
    out(f"  {verb} (one copy each)                       : {len(restored)}  ({human(bytes_r)})")
    if restored:
        out(f"  true duplicates left in quarantine           : "
            f"{sum(r['true_duplicates_left'] for r in restored)}")
    if failed:
        out(f"  failed to restore                            : {failed}")
    if inconclusive or syncing:
        out(f"  NOT touched (inconclusive {inconclusive}, syncing {syncing}) — re-run verify later")
    if poisoned:
        out(f"  NOT healed — {poisoned} file(s) have an INVALID placeholder hash "
            f"(unverifiable identity, likely wrongly quarantined). Heal can't pick a "
            f"safe original for them; run `undo` to restore them by manifest, then "
            f"re-scan once the files have downloaded.")
    for r in restored[:30]:
        extra = f"   (+{r['true_duplicates_left']} dup left)" if r["true_duplicates_left"] else ""
        out(f"    {'would restore' if dry else 'restored'}: {safe(r['restored_to'])}{extra}")
    if restored and not dry:
        out("\n  Recovered files logged in each Duplicates/_heal_log.csv. "
            "Run `verify` to confirm the folder is now clean.")
    elif not restored:
        out("\n  Nothing to recover — no orphaned files found.")
    emit_json(result)
    return 0

def _contained(path, base):
    rp = os.path.realpath(path)
    rb = os.path.realpath(base)
    return rp == rb or rp.startswith(rb + os.sep)

def _resolve_stored(dd, stored):
    """Find the quarantined file a manifest row points at, undoing csv_safe's
    defusing. For manifests written by the current (quote-doubling, injective)
    writer, csv_unsafe(stored) is ALWAYS the exact on-disk name — so probe the
    de-defused name FIRST. Probing the raw value first could grab the WRONG
    file when one row's raw stored_as equals another quarantined file's real
    name (e.g. defused '-x.jpg vs a genuine '-x.jpg). The raw probe remains
    only as a legacy fallback for pre-doubling manifests. Returns
    (path, existed)."""
    alt_name = csv_unsafe(stored)
    if alt_name != stored:
        alt = os.path.join(dd, alt_name)
        if os.path.exists(alt):
            return alt, True
    raw = os.path.join(dd, stored)
    if os.path.exists(raw):
        return raw, True
    return os.path.join(dd, alt_name), False

def cmd_undo(args):
    root = check_target(args.folder)
    restored = skipped = refused = unaccounted = 0
    # undo renames files back; take the single-writer lock so it can't race a
    # concurrent `move`/`heal` (which could otherwise retire a live manifest).
    with Lock(root) as lock:
        for dd in find_dup_dirs(root, managed_only=True):
            lock.touch()
            parent = os.path.dirname(dd)
            # A live run holds its own lock INSIDE this subtree (e.g. a `move`
            # scoped to the subfolder): leave that Duplicates dir alone.
            if _foreign_lock_between(root, parent):
                out(f"  SKIPPED (a dedupe run is active in "
                    f"{safe(os.path.relpath(parent, root))}) — re-run undo later")
                continue
            mp = os.path.join(dd, MANIFEST)
            all_done = True
            try:
                with open(mp, newline="") as f:
                    for row in csv.DictReader(f):
                        # De-defuse the two operational path columns so names that
                        # csv_safe protected (leading = + - @) round-trip correctly.
                        orig = csv_unsafe(row["original_relpath"])
                        src, src_exists = _resolve_stored(dd, row["stored_as"])
                        dst = os.path.join(parent, orig)
                        # containment: refuse any manifest row that escapes the tree
                        if os.path.isabs(orig) or not _contained(dst, parent) \
                                or not _contained(src, dd):
                            refused += 1; all_done = False
                            out(f"  REFUSED (escapes folder): {safe(orig)}")
                            continue
                        if not src_exists:
                            # The quarantined file isn't here under either name. If it
                            # already sits at its origin it was restored earlier (fine);
                            # otherwise the row is UNACCOUNTED — block retirement so a
                            # stranded file can never fall out of the safety net, and
                            # SAY so: a 0/0/0 summary must never hide a stranded row.
                            if not os.path.exists(dst):
                                unaccounted += 1; all_done = False
                                out(f"  UNACCOUNTED: {safe(orig)} — not in quarantine "
                                    f"and not at its origin; manifest kept")
                            continue
                        if os.path.exists(dst):
                            skipped += 1; all_done = False
                            continue
                        try:
                            os.makedirs(os.path.dirname(dst), exist_ok=True)
                            os.rename(src, dst)
                            restored += 1
                        except OSError as err:
                            refused += 1; all_done = False
                            out(f"  could not restore {safe(orig)}: {err.__class__.__name__}")
            except (OSError, csv.Error, KeyError, TypeError):
                # manifest vanished/unreadable/corrupt (before OR mid-read): skip
                # this dir and never retire it — rows may be unaccounted for.
                all_done = False
                out(f"  WARNING: could not read manifest in "
                    f"{safe(os.path.relpath(dd, root))} — skipping")
            # Only retire the manifest when everything it lists is truly restored,
            # and never clobber a prior .restored.
            if all_done:
                dest = mp + ".restored"
                if not os.path.exists(dest):
                    try:
                        os.rename(mp, dest)
                    except OSError:
                        pass
    out(f"UNDO: restored {restored} files, skipped {skipped} "
        f"(destination already existed), refused {refused} (unsafe/failed)"
        + (f", {unaccounted} UNACCOUNTED (investigate!)" if unaccounted else ""))
    emit_json({"action": "undo", "version": __version__,
               "restored": restored, "skipped": skipped, "refused": refused,
               "unaccounted": unaccounted})
    return 0

def cmd_where(args):
    """Per-file ground truth for "is this quarantined file REALLY a duplicate,
    and where does its twin live?". For every file in Duplicates (or only the
    names given on the command line), lists each byte-identical copy that exists
    OUTSIDE the quarantine — by CONTENT, so a renamed twin is still found — and
    cross-checks the manifest's recorded keeper. Read-only; changes nothing.

    Verdicts per file:
      true-duplicate  identical cop(ies) exist outside — safe to delete
      NOT-a-duplicate content exists NOWHERE outside (orphan) — heal restores it
      unverifiable    carries a placeholder/sentinel hash (one hash ↔ many
                      sizes) — identity unknowable until materialized
      inconclusive / syncing / unreadable — as in `verify`

    Exit 0 when every reported file is a true duplicate; exit 2 otherwise."""
    root = check_target(args.folder)
    scope = args.scope
    if scope is None:
        scope = detect_scope(root) or "single"
    wanted = {nfc(n) for n in (getattr(args, "names", None) or [])}
    with tempfile.TemporaryDirectory() as wd:
        dbx = make_dbx(root, wd)
        sc = Scanner(root, dbx, quiet=args.quiet, recent_secs=args.recent_secs,
                     label="where")
        q = scan_quarantine(root, scope, sc, dbx)
        twins = defaultdict(list)                    # hash -> outside rel paths
        for e in q["outside"]:
            twins[e["hash"]].append(e["rel"])
        # manifest cross-reference: stored name -> the keeper move recorded
        claimed = {}
        for dd in q["dup_dirs"]:
            mp = os.path.join(dd, MANIFEST)
            for path in (mp, mp + ".restored"):
                if not os.path.exists(path):
                    continue
                try:
                    with open(path, newline="") as f:
                        for row in csv.DictReader(f):
                            keeper = csv_unsafe(row.get("keeper_relpath") or "")
                            stored = row.get("stored_as") or ""
                            for cand in {stored, csv_unsafe(stored)}:
                                if cand:
                                    claimed[(dd, nfc(cand))] = keeper
                except (OSError, csv.Error, KeyError, TypeError):
                    pass
        rows = []
        for r in q["records"]:
            if wanted and nfc(r["name"]) not in wanted:
                continue
            t = sorted(twins.get(r["hash"], [])) if r["hash"] else []
            keeper = claimed.get((r["dup_dir"], nfc(r["name"])))
            kstatus = None
            if keeper:
                base = root if scope == "single" else os.path.dirname(r["dup_dir"])
                kpath = os.path.join(base, keeper)
                if os.path.isabs(keeper) or not _contained(kpath, root):
                    kstatus = "unsafe-path"
                elif os.path.exists(kpath):
                    kstatus = "present"
                else:
                    kstatus = "missing"
            rows.append({"file": os.path.relpath(r["path"], root),
                         "verdict": r["klass"], "size": r["size"],
                         "copies_outside": t[:5],
                         "copies_outside_total": len(t),
                         "manifest_keeper": keeper,
                         "keeper_status": kstatus})
        # unmatched queries deserve an explicit answer, not silence
        found = {nfc(os.path.basename(row["file"])) for row in rows}
        missing_queries = sorted(n for n in wanted if n not in found)
    order = {"orphan": 0, "poisoned": 1, "unreadable": 2, "inconclusive": 3,
             "syncing": 4, "ok": 5}
    rows.sort(key=lambda x: (order.get(x["verdict"], 9), x["file"]))
    counts = Counter(x["verdict"] for x in rows)
    all_good = not rows or set(counts) <= {"ok"}
    result = {"action": "where", "scope": scope, "version": __version__,
              "queried": sorted(wanted) if wanted else "ALL",
              "reported": len(rows), "counts": dict(counts),
              "not_found_in_quarantine": missing_queries,
              "files": rows[:500], "ok": all_good}
    VERDICT_TEXT = {
        "ok": "TRUE DUPLICATE",
        "orphan": "NOT A DUPLICATE — content exists NOWHERE outside",
        "poisoned": "UNVERIFIABLE — placeholder/sentinel hash",
        "inconclusive": "inconclusive — same-size unindexed file outside",
        "syncing": "syncing — cloud hash not yet available",
        "unreadable": "UNREADABLE",
    }
    out(f"\nWHERE of {safe(root)}  (scope: {scope})"
        + (f"  — {len(wanted)} file(s) queried" if wanted else "  — all quarantined files"))
    out(f"  reported: {len(rows)}   " +
        "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for x in rows[:40]:
        out(f"\n  {safe(x['file'])}  [{VERDICT_TEXT.get(x['verdict'], x['verdict'])}]")
        if x["copies_outside"]:
            for tw in x["copies_outside"]:
                out(f"      identical copy: {safe(tw)}")
            if x["copies_outside_total"] > len(x["copies_outside"]):
                out(f"      ... and {x['copies_outside_total'] - len(x['copies_outside'])} more")
        elif x["verdict"] == "orphan":
            out( "      no identical copy anywhere outside quarantine")
        if x["manifest_keeper"]:
            note = {"present": "exists", "missing": "GONE",
                    "unsafe-path": "UNSAFE PATH"}.get(x["keeper_status"], "?")
            hint = ""
            if x["verdict"] == "orphan" and x["keeper_status"] == "present":
                hint = "  <- keeper exists but is NOT identical (sentinel-hash mis-file)"
            out(f"      manifest claimed keeper: {safe(x['manifest_keeper'])} ({note}){hint}")
    if len(rows) > 40:
        out(f"\n  ... {len(rows) - 40} more (full list in RESULT_JSON, capped at 500)")
    for n in missing_queries:
        out(f"  NOT FOUND in quarantine: {safe(n)}")
    if not all_good:
        out("\n  Some quarantined files are NOT proven duplicates — run `heal` to "
            "restore orphans; materialize placeholder-hash files then re-scan.")
    emit_json(result)
    return 0 if all_good else 2

class _Parser(argparse.ArgumentParser):
    def error(self, message):        # usage errors exit 64, not 2 (reserved for verify)
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(64)

def main():
    global _JSON_ONLY
    ap = _Parser(description=__doc__,
                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"check-for-dupes {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("scan", cmd_scan), ("move", cmd_move),
                     ("verify", cmd_verify), ("where", cmd_where),
                     ("heal", cmd_heal), ("undo", cmd_undo)):
        p = sub.add_parser(name, formatter_class=argparse.RawDescriptionHelpFormatter)
        p.add_argument("folder", help="target folder (absolute path recommended)")
        if name == "where":
            p.add_argument("names", nargs="*",
                           help="quarantined filename(s) to look up (default: all)")
        p.add_argument("--json", action="store_true",
                       help="machine mode: only RESULT_JSON on stdout, all else on stderr")
        p.add_argument("--quiet", action="store_true", help="suppress progress ticks")
        p.add_argument("--recent-secs", type=int, default=DEFAULT_RECENT_SECS,
                       help="skip files modified within N seconds (default 120)")
        if name in ("scan", "move", "verify", "heal", "where"):
            # verify/heal/where default to None so they auto-detect the recorded scope
            p.add_argument("--scope", choices=["single", "per-folder"],
                           default=(None if name in ("verify", "heal", "where")
                                    else "single"))
        if name == "scan":
            p.add_argument("--show", type=int, default=0,
                           help="print up to N sample moves")
        if name == "move":
            p.add_argument("--max-passes", type=int, default=10)
            p.add_argument("--dry-run", action="store_true",
                           help="alias for `scan` — report, move nothing")
        if name == "heal":
            p.add_argument("--dry-run", action="store_true",
                           help="report what would be recovered, move nothing")
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    _JSON_ONLY = getattr(args, "json", False)
    if args.quiet or _JSON_ONLY:
        args.quiet = True
    # Normalize the target so a relative path works; leading-'-' paths must be
    # given as './-name' (argparse consumes a bare -name as a flag).
    args.folder = os.path.abspath(os.path.expanduser(args.folder))
    if not hasattr(args, "show"):
        args.show = 0
    sys.exit(args.fn(args))

if __name__ == "__main__":
    main()
