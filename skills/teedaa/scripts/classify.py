#!/usr/bin/env python3
"""teedaa classifier cascade — turns the raw metadata index into verdicts.

Three passes over the index (memory-bounded, works at millions of files):
  A. stream files grouped by folder -> per-folder marker facts
  B. top-down over folders -> protected-zone assignment (dirs.prot/zone)
  C. stream files again -> per-file class (files.cls), using sibling context

Class codes written to files.cls (single column, precedence applied):
  sn:S1:<cat> / sn:S2:<cat>   sensitive (cat: cred fin id med leg int bak cry)
  jA:<fam> jB:<fam> jC:<fam>  junk tiers A(certain) B(probable) C(review),
                              fam = J1..J13 family code
  ph:cert ph:likely ph:poss   personal photo confidence tiers
  ph:recv ph:screen ph:asset  received media / screenshot / app asset
  ph:unk                      image with no signals that is dataless (defer)
  NULL                        ordinary keep
Folder zones written to dirs.zone (root) + dirs.prot (root id, inherited):
  git project bundle vm db iosbackup appdata steam library homebackup
Rule zero note: this module only ever writes cls/zone/prot columns in the
INDEX database — never anything in the scanned tree.
"""
import os, re, sqlite3, sys, time
from collections import defaultdict

# ---------------- protected-zone tables (digest §2) --------------------------
VCS_DIRS = {".git", ".hg", ".svn", ".bzr", ".jj", "_darcs", ".fossil"}
VCS_FILES = {".git", "_fossil_", ".fslckout"}   # .git can be a FILE (worktree)

STRONG_MANIFESTS = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "poetry.lock",
    "uv.lock", "cargo.toml", "go.mod", "cmakelists.txt", "configure.ac",
    "configure.in", "meson.build", "pom.xml", "build.gradle",
    "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradlew",
    "mix.exs", "composer.json", "gemfile", "rakefile", "package.swift",
    "pubspec.yaml", "deno.json", "deno.jsonc", "stack.yaml", "project.toml",
    "rebar.config", "build.sbt", "flake.nix", ".projectile",
}
STRONG_SUFFIXES = (".sln", ".csproj", ".fsproj", ".vbproj", ".cabal")
STRONG_DIR_SUFFIXES = (".xcodeproj", ".xcworkspace", ".playground")
WEAK_MARKERS = {"makefile", "gnumakefile", "requirements.txt",
                "environment.yml", "dockerfile", "docker-compose.yml",
                "tsconfig.json", ".editorconfig"}
WEAK_DIRS = {".idea", ".vscode"}
CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".c", ".h", ".cpp", ".cc",
             ".hpp", ".m", ".mm", ".swift", ".java", ".kt", ".go", ".rs",
             ".rb", ".php", ".cs", ".sh", ".pl", ".lua", ".scala", ".hs",
             ".ex", ".exs", ".clj", ".ml", ".r", ".jl", ".dart", ".vue",
             ".svelte", ".sql"}

BUNDLE_DIR_EXTS = (
    # code bundles
    ".app", ".framework", ".bundle", ".plugin", ".appex", ".xpc", ".kext",
    ".systemextension", ".dext", ".qlgenerator", ".mdimporter", ".prefpane",
    ".saver", ".wdgt", ".component", ".vst", ".vst3", ".aaxplugin",
    ".docset", ".lproj", ".nib", ".storyboardc", ".scptd", ".workflow",
    ".action", ".caction", ".definition", ".osax", ".pkg", ".mpkg",
    ".xctest", ".swiftpm", ".appiconset", ".xcassets", ".dst",
    # media libraries (highest value)
    ".photoslibrary", ".migratedphotolibrary", ".aplibrary", ".iphotolibrary",
    ".musiclibrary", ".tvlibrary", ".imovielibrary", ".theater",
    ".fcpbundle", ".fcpevent", ".logicx", ".band", ".cst",
    # document packages
    ".pages", ".key", ".numbers", ".rtfd", ".graffle", ".sketch", ".scriv",
    ".bbprojectd", ".oo3", ".ofocus", ".dtbase2", ".journal", ".textbundle",
    ".abbu", ".mbox", ".help", ".bentodb", ".quiver", ".qvnotebook",
    # VM / disk images
    ".sparsebundle", ".vmwarevm", ".pvm", ".utm",
)
LIBRARY_EXTS = {".photoslibrary", ".migratedphotolibrary", ".aplibrary",
                ".iphotolibrary", ".musiclibrary", ".tvlibrary",
                ".imovielibrary", ".theater", ".fcpbundle", ".fcpevent",
                ".logicx", ".band"}
VM_EXTS = {".sparsebundle", ".vmwarevm", ".pvm", ".utm"}

DB_MARKER_FILES = {"pg_version", "ibdata1", "wiredtiger", "wiredtiger.wt",
                   "storage.bson", "metadata.db"}
IOS_BACKUP_FILES = {"manifest.plist", "manifest.db"}
ANKI_FILES = {"collection.anki2", "prefs21.db"}

REBUILDABLE_DIRS = {"node_modules": "package.json", "pods": "podfile",
                    "bower_components": None, ".next": None, ".nuxt": None}
VENV_NAMES = {"venv", ".venv", "env", "virtualenv"}
CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache",
                   ".ruff_cache", ".tox", ".sass-cache", ".cache",
                   ".parcel-cache", ".turbo", "deriveddata", ".thumbnails"}

# ---------------- junk tables (digest §1) ------------------------------------
OS_DROPPINGS = {".ds_store", "thumbs.db", "ehthumbs.db", "ehthumbs_vista.db",
                ".localized", ".apdisk", ".com.apple.timemachine.donotpresent"}
OS_DROPPINGS_B = {"desktop.ini", "icon\r"}
PARTIAL_EXTS = (".crdownload", ".part", ".partial", ".opdownload",
                ".wkdownload", ".aria2", ".filepart", ".!ut", ".bc!",
                ".!qb", ".!bt")
MULTIVOL_RE = re.compile(r"\.(part\d+\.rar|z\d\d|r\d\d)$", re.I)
# include macro-enabled and template extensions (audit: office-lock coverage)
OFFICE_LOCK_RE = re.compile(
    r"^~\$.*\.(docx?|docm|dotx?|dotm|xls[xmb]?|xlt[xm]?|ppt[xm]?|pot[xm]?|"
    r"pps[xm]?)$", re.I)
SWAP_RE = re.compile(r"(\.sw[pon]$)|(~$)|(^#.+#$)|(^\.#)", )
# .ips also names ROM-hack patch files; require an Apple-report shape below.
CRASH_EXTS = (".crash", ".spin", ".hang", ".diag", ".dpsub", ".stackdump")
# minidump must be a real dump name, not any file starting with the word.
CRASH_RE = re.compile(
    r"(^minidump[-_.]?\d)|(^minidump.*\.(dmp|mdmp)$)|"
    r"(^hs_err_pid\d+\.log$)|(^core(\.\d+)?$)", re.I)
# .ips is auto-junk only when it looks like an Apple crash report
IPS_CRASH_RE = re.compile(r"^.+-\d{4}-\d{2}-\d{2}-\d{6}.*\.ips$", re.I)
INSTALLER_EXTS = (".dmg", ".pkg", ".mpkg", ".msi", ".msix", ".appx", ".deb",
                  ".rpm", ".appimage")
INSTALLER_EXE_RE = re.compile(
    r"(?i)(setup|install|installer|_x64|_x86|win(32|64)|v?\d+\.\d+)")
ARCHIVE_EXTS = (".zip", ".rar", ".7z", ".tgz", ".sit", ".sitx", ".tar.gz",
                ".tar.bz2", ".tar.xz")
DUPNAME_RES = [re.compile(r"^(.+) \((\d{1,3})\)(\.[^.]*)?$"),
               re.compile(r"^(.+?) copy( \d+)?(\.[^.]*)?$", re.I),
               re.compile(r"^(.+?) - Copy( \(\d+\))?(\.[^.]*)?$", re.I),
               re.compile(r"^Copy of (.+)$", re.I),
               re.compile(r"^(.+) \(.+'s conflicted copy \d{4}-\d{2}-\d{2}"
                          r"( \(\d+\))?\)(\.[^.]*)?$"),
               re.compile(r"^(.+) \(Case Conflict( \d+)?\)(\.[^.]*)?$"),
               re.compile(r"^(.+) \(Unicode Encoding Conflict\)(\.[^.]*)?$")]
ZERO_OK = {"__init__.py", ".gitkeep", ".keep", ".nomedia", ".placeholder",
           ".hushlogin", ".metadata_never_index", ".gitignore", "lock"}
LOG_RE = re.compile(r"\.log(\.\d+)?(\.gz)?$|\.out$", re.I)

STALE_PARTIAL_SECS = 14 * 86400
STALE_LOCK_SECS = 7 * 86400
DORMANT_PROJECT_SECS = 540 * 86400          # ~18 months
VINTAGE_YEAR = 2005
GARBAGE_YEARS = {1904, 1970, 1980}


def year_of(mtime, now=None):
    if not mtime or mtime <= 0:
        return None
    try:
        y = time.gmtime(mtime).tm_year
    except (OverflowError, OSError, ValueError):
        return None
    if y in GARBAGE_YEARS or (now and mtime > now + 86400 * 365):
        return None
    return y


def _ext(name):
    n = name.lower()
    for multi in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if n.endswith(multi):
            return multi
    i = n.rfind(".")
    return n[i:] if i > 0 else ""


def _stem(name):
    e = _ext(name)
    return name[:len(name) - len(e)] if e else name


# ---------------- data carriers ----------------------------------------------
class DirNode:
    __slots__ = ("id", "parent", "name", "depth", "status", "zone", "prot",
                 "child_dirs", "facts", "file_count", "empty")

    def __init__(self, id_, parent, name, depth, status):
        self.id = id_
        self.parent = parent
        self.name = name or ""
        self.depth = depth
        self.status = status
        self.zone = None       # zone kind when this dir IS a zone root
        self.prot = None       # id of owning zone root (self included)
        self.child_dirs = []
        self.facts = None      # set of lowercase marker facts from pass A
        self.file_count = 0
        self.empty = False


ZONE_STATSKEYS = ("git", "project", "bundle", "vm", "db", "iosbackup",
                  "appdata", "steam", "library", "homebackup")


def load_dirs(con):
    nodes = {}
    for did, parent, name, depth, status in con.execute(
            "SELECT id, parent, name, depth, status FROM dirs"):
        nodes[did] = DirNode(did, parent, name, depth, status)
    root = None
    for n in nodes.values():
        if n.parent is None:
            root = n
        else:
            p = nodes.get(n.parent)
            if p:
                p.child_dirs.append(n.id)
    return nodes, root


def stream_files_by_dir(con, columns="rowid, parent, name, size, mtime, "
                                     "dataless, link_target, hash, nlink"):
    """Yield (parent_id, [row, ...]) groups, ordered by parent."""
    cur = con.execute(f"SELECT {columns} FROM files ORDER BY parent")
    group = []
    gparent = None
    for row in cur:
        parent = row[1]
        if parent != gparent and group:
            yield gparent, group
            group = []
        gparent = parent
        group.append(row)
    if group:
        yield gparent, group


# ---------------- pass A: per-dir marker facts -------------------------------
def pass_a_facts(con, nodes):
    """Collect the small per-dir fact set zone detection needs."""
    for parent, rows in stream_files_by_dir(con, "rowid, parent, name"):
        node = nodes.get(parent)
        if node is None:
            continue
        facts = set()
        weak = 0
        code_files = 0
        for _rowid, _p, name in rows:
            if name is None:
                continue
            low = name.lower()
            if low in STRONG_MANIFESTS or low.endswith(STRONG_SUFFIXES):
                facts.add("strong")
            elif low in WEAK_MARKERS:
                weak += 1
            elif low in VCS_FILES:
                facts.add("vcs")
            elif low in DB_MARKER_FILES:
                facts.add("db")
            elif low in IOS_BACKUP_FILES:
                facts.add("iosbackup-file")
            elif low in ANKI_FILES:
                facts.add("appdata")
            elif low == "info.plist":
                facts.add("infoplist")
            elif low == "cachedir.tag":
                facts.add("cachedir")
            elif low == "current":
                facts.add("leveldb-current")
            elif low.startswith("manifest-"):
                facts.add("leveldb-manifest")
            elif low.endswith((".lrcat", ".cosessiondb", ".cocatalogdb")):
                facts.add("appdata")
            elif low == "pyvenv.cfg":
                facts.add("venv")
            elif low.endswith(".vbox"):
                facts.add("vm")
            if _ext(low) in CODE_EXTS:
                code_files += 1
        node.file_count = len(rows)
        if weak >= 2 or (weak >= 1 and code_files > 0):
            facts.add("weakproject")
        if code_files:
            facts.add("hascode")
        node.facts = facts or None


# ---------------- pass B: zone assignment (top-down) -------------------------
def pass_b_zones(nodes, root):
    stats = defaultdict(int)
    order = sorted(nodes.values(), key=lambda n: n.depth)
    child_dirnames = {}
    for n in order:
        child_dirnames[n.id] = {nodes[c].name.lower() for c in n.child_dirs}
    for n in order:
        parent = nodes.get(n.parent)
        if parent is not None and parent.prot is not None:
            n.prot = parent.prot     # inherited; nested markers stay recorded
            continue
        kind = None
        low = n.name.lower()
        cnames = child_dirnames[n.id]
        facts = n.facts or set()
        # channel 3: bundle dirname extension (checked first: cheap + decisive)
        for ext in BUNDLE_DIR_EXTS:
            if low.endswith(ext):
                if ext in LIBRARY_EXTS:
                    kind = "library"
                elif ext in VM_EXTS:
                    kind = "vm"
                else:
                    kind = "bundle"
                break
        # channel 1: VCS
        if kind is None and (cnames & VCS_DIRS or "vcs" in facts or
                             low.endswith(".git")):
            kind = "git"
        # channel 2: project manifests
        if kind is None and ("strong" in facts or "weakproject" in facts or
                             any(c.endswith(STRONG_DIR_SUFFIXES)
                                 for c in cnames) or
                             (WEAK_DIRS & cnames and "hascode" in facts) or
                             "ableton project info" in cnames):
            kind = "project"
        # channel 3b: structural shapes
        if kind is None:
            if "infoplist" in facts and "bands" in cnames:
                kind = "vm"
            elif "vm" in facts:
                kind = "vm"
            elif "db" in facts or ("leveldb-current" in facts and
                                   "leveldb-manifest" in facts):
                kind = "db"
            elif "iosbackup-file" in facts and "infoplist" in facts:
                kind = "iosbackup"
            elif "appdata" in facts:
                kind = "appdata"
            elif "steamapps" in cnames:
                kind = "steam"
            elif low == "library" and {"application support",
                                       "preferences"} <= cnames:
                kind = "homebackup"
        if kind:
            n.zone = kind
            n.prot = n.id
            stats[kind] += 1
    return stats


def _empty_bottom_up(nodes):
    for n in sorted(nodes.values(), key=lambda x: -x.depth):
        if n.file_count == 0 and n.prot is None and \
                all(nodes[c].empty for c in n.child_dirs) and \
                n.status in ("done",):
            n.empty = True
    return sum(1 for n in nodes.values() if n.empty)


# ---------------- pass C: per-file junk classification -----------------------
def _apps_installed():
    """Lowercased, version-token-stripped names of /Applications apps."""
    apps = set()
    try:
        for n in os.listdir("/Applications"):
            if n.lower().endswith(".app"):
                base = re.sub(r"[\s_-]*v?\d[\d.]*$", "", n[:-4].lower())
                apps.add(base.strip())
    except OSError:
        pass
    return apps


VERSION_TOKEN_RE = re.compile(
    r"(?i)[\s._-]*(v?\d[\d.]*|x64|x86|arm64|aarch64|universal|mac(os)?|"
    r"win(32|64)?|setup|installer|install)")


def _installer_app_guess(stem):
    return VERSION_TOKEN_RE.sub("", stem).strip(" ._-").lower()


class JunkContext:
    """Sibling context for one directory (built per group in pass C)."""

    def __init__(self, node, rows, child_dirnames, now):
        self.node = node
        self.now = now
        self.names = set()
        self.stems = set()
        self.sizes = {}
        self.hashes = {}
        for r in rows:
            name = r[2]
            if name is None:
                continue
            self.names.add(name)
            self.stems.add(_stem(name).lower())
            self.sizes[name] = r[3]
            if len(r) > 7 and r[7]:
                self.hashes[name] = r[7]
        self.child_dirnames = child_dirnames
        self.lownames = {n.lower() for n in self.names}


def classify_junk(name, size, mtime, dataless, link_target, ctx,
                  apps_installed):
    """Return (tier, family) or None. Tier in 'A' 'B' 'C'."""
    low = name.lower()
    ext = _ext(name)
    stem = _stem(name)
    now = ctx.now
    age = (now - mtime) if mtime else 0

    # J1 OS droppings
    if low in OS_DROPPINGS:
        return "A", "J1"
    if low in OS_DROPPINGS_B:
        return "B", "J1"
    if name.startswith("._"):
        sib = name[2:]
        y = year_of(mtime, now)
        if sib in ctx.names and (y is None or y >= 2002):
            return "A", "J1"
        return "C", "J1"    # classic-Mac resource fork may BE the data

    # J2 partial downloads (multi-volume archives excluded)
    if MULTIVOL_RE.search(low):
        return None
    if low.endswith(PARTIAL_EXTS):
        if age > STALE_PARTIAL_SECS:
            return "A", "J2"
        return None          # fresh: an active download, not junk
    if ext == ".torrent":
        return "B", "J2"

    # J3 editor/office temp & locks
    if OFFICE_LOCK_RE.match(name):
        return ("A", "J3") if age > STALE_LOCK_SECS else None
    if SWAP_RE.search(name) and not name.startswith("._"):
        return ("A", "J3") if age > STALE_LOCK_SECS else None
    if ext in (".asd", ".wbk", ".xlk"):
        healthy = stem.lower() in {_stem(n).lower()
                                   for n in ctx.names if n != name}
        return ("B", "J3") if healthy else ("C", "J3")
    if ext in (".tmp", ".temp"):
        same_stem = stem.lower() in {_stem(n).lower()
                                     for n in ctx.names if n != name}
        if size and size > 0 and not same_stem:
            return "A", "J3"
        return "B", "J3"

    # J5 crash logs / dumps
    if ext in CRASH_EXTS or CRASH_RE.match(name):
        if low.startswith("core") and not (size or 0) > 1 << 20:
            return "C", "J5"
        return "A", "J5"
    if ext == ".ips":
        # Apple-report-shaped .ips is certain junk; anything else (ROM-hack
        # patches share the extension) is review-only.
        return ("A", "J5") if IPS_CRASH_RE.match(name) else ("C", "J5")
    if ext == ".dmp":
        return "B", "J5"

    # J6 installers
    if ext in INSTALLER_EXTS or (ext == ".exe" and
                                 INSTALLER_EXE_RE.search(stem)):
        y = year_of(mtime, now)
        if y is not None and y < 2015:
            return "C", "J6"          # abandonware: may be unobtainable now
        guess = _installer_app_guess(stem)
        if guess and guess in apps_installed:
            return "A", "J6"
        return "B", "J6"

    # J7 archive with extracted twin (always C: surface, decide nothing)
    if ext in ARCHIVE_EXTS:
        twin = _stem(name)
        if twin.lower() in ctx.child_dirnames:
            return "C", "J7"
        return None

    # J8 duplicate-name / sync-conflict
    for rx in DUPNAME_RES:
        m = rx.match(name)
        if m:
            # reconstruct candidate base name: strip the matched decoration
            cand = None
            g1 = m.group(1)
            e = _ext(name)
            for candidate in (g1 + e, g1):
                if candidate in ctx.names and candidate != name:
                    cand = candidate
                    break
            if cand is None:
                return "C", "J8"
            h1, h2 = ctx.hashes.get(name), ctx.hashes.get(cand)
            if h1 and h2 and h1 == h2:
                return "A", "J8"      # content-proven twin (nucleus hashes)
            if ctx.sizes.get(name) is not None and \
                    ctx.sizes.get(name) == ctx.sizes.get(cand):
                return "B", "J8"
            return "C", "J8"

    # J9 zero-byte
    if size == 0 and not dataless and low not in ZERO_OK and \
            not low.endswith(".lock") and link_target is None:
        return "A", "J9"

    # J10 broken symlinks (aliases are handled as C by the photo/asset pass)
    if link_target is not None and link_target != "":
        target = link_target if os.path.isabs(link_target) else None
        if target is None:
            return None      # relative links resolved by caller (needs path)
        if re.match(r"^[A-Za-z]:\\\\", link_target) or \
                link_target.startswith("/Volumes/"):
            return "C", "J10"
        if not os.path.lexists(target):
            return "B", "J10"
        return None

    # J13 rotated logs & patch detritus
    if LOG_RE.search(low):
        y = year_of(mtime, now)
        if y is not None and y < 2010:
            return "C", "J13"        # early-internet logs are history, review
        return "B", "J13"
    if ext in (".orig", ".rej"):
        return "B", "J13"
    if ext in (".pyc", ".pyo"):
        return "A", "J13"

    return None


def vintage_demote(tier, mtime, now):
    """Pre-2005 files drop one tier: old ≠ junk, ever (1995-era corpus)."""
    y = year_of(mtime, now)
    if y is not None and y < VINTAGE_YEAR:
        return {"A": "B", "B": "C"}.get(tier, "C")
    return tier


# cache/rebuildable DIR verdicts land on files inside them via the dir zone;
# handled in classify_index (a whole cache dir is one aggregate, per digest).
def dir_junk_kind(node, nodes, child_dirnames_lower, facts):
    low = node.name.lower()
    if low in CACHE_DIR_NAMES or (facts and "cachedir" in facts):
        return "cache"
    if low in REBUILDABLE_DIRS:
        need = REBUILDABLE_DIRS[low]
        parent = nodes.get(node.parent)
        if need is None:
            return "rebuildable"
        if parent and parent.facts and "strong" in parent.facts:
            return "rebuildable"
        return None                  # bare-name without manifest: leave alone
    if low in VENV_NAMES and facts and "venv" in facts:
        return "rebuildable"
    if low == "__macosx":
        return "cache"
    return None


# ---------------- driver -----------------------------------------------------
def classify_index(con, root, quiet=True):
    """Run the full cascade. Returns a stats dict. Writes files.cls,
    dirs.zone/prot/empty/jkind columns (schema v3)."""
    now = time.time()
    nodes, rootnode = load_dirs(con)
    pass_a_facts(con, nodes)
    zstats = pass_b_zones(nodes, rootnode)
    empties = _empty_bottom_up(nodes)

    # dir-level junk kinds (cache/rebuildable) — only OUTSIDE protected zones
    child_dirnames_lower = {n.id: {nodes[c].name.lower()
                                   for c in n.child_dirs}
                            for n in nodes.values()}
    # cache/rebuildable is an OVERLAY, not a protection: a node_modules needs
    # its project's package.json to even qualify (digest J4), so it must be
    # markable INSIDE protected zones — prot root stays with the project.
    for n in nodes.values():
        if n.zone is not None:
            continue
        kind = dir_junk_kind(n, nodes, child_dirnames_lower, n.facts)
        if kind:
            n.zone = kind
            if n.prot is None:
                n.prot = n.id
            zstats[kind] += 1

    con.executemany("UPDATE dirs SET zone=?, prot=?, empty=? WHERE id=?",
                    [(n.zone, n.prot, 1 if n.empty else 0, n.id)
                     for n in nodes.values()])
    con.commit()

    apps = _apps_installed()
    from sensitive import classify_sensitive   # part 4d (same directory)
    from photos import classify_photo          # part 4c (same directory)

    stats = defaultdict(int)
    stats.update({"zones_" + k: v for k, v in zstats.items()})
    stats["empty_dirs"] = empties
    updates = []
    dir_paths_cache = {}

    def dir_path(did):
        if did in dir_paths_cache:
            return dir_paths_cache[did]
        chain = []
        cur = did
        while cur is not None:
            n = nodes[cur]
            if n.parent is None:
                base = root
                break
            chain.append(n.name)
            cur = n.parent
        p = os.path.join(root, *reversed(chain)) if chain else root
        dir_paths_cache[did] = p
        return p

    for parent, rows in stream_files_by_dir(con):
        node = nodes.get(parent)
        if node is None:
            continue
        protected = node.prot is not None
        zone_kind = nodes[node.prot].zone if protected else None
        ctx = JunkContext(node, rows, child_dirnames_lower.get(parent, set()),
                          now)
        dpath = dir_path(parent)
        for row in rows:
            rowid, _p, name, size, mtime, dataless, link_target, h, nlink = row
            if name is None:
                continue
            cls = None
            sens = classify_sensitive(name, size, dpath, ctx,
                                      in_code_tree=(zone_kind in
                                                    ("git", "project")))
            if sens:
                cls = sens
            elif protected:
                cls = None           # zone suppresses junk/photo suggestions
            else:
                j = classify_junk(name, size, mtime, dataless, link_target,
                                  ctx, apps)
                if j is None and link_target:
                    # relative symlink: resolve against the dir path (lstat
                    # only — lexists never materializes placeholders)
                    tgt = os.path.normpath(os.path.join(dpath, link_target))
                    if not os.path.lexists(tgt):
                        j = ("B", "J10")
                # A tier-C J8 verdict ("X (2).jpg" with no content/size-proven
                # twin) must NOT bury a real burst/export photo — fall through
                # to the photo classifier for image/video names and prefer a
                # concrete photo verdict. (audit QUA-a48735b8)
                if j and j == ("C", "J8"):
                    ph = classify_photo(name, size, mtime, dataless, dpath, ctx,
                                        root)
                    if ph and ph.startswith("ph:") and ph != "ph:asset":
                        j = None
                        cls = ph
                if j:
                    tier, fam = j
                    tier = vintage_demote(tier, mtime, now)
                    cls = f"j{tier}:{fam}"
                elif cls is None:
                    cls = classify_photo(name, size, mtime, dataless, dpath,
                                         ctx, root)
                    # orphan-sidecar junk from the photo pass also demotes for
                    # vintage files, like every other junk verdict.
                    if cls and cls.startswith("j"):
                        t, fam = cls[1], cls.split(":")[1]
                        cls = f"j{vintage_demote(t, mtime, now)}:{fam}"
            if cls:
                updates.append((cls, rowid))
                key = cls.split(":", 1)[0]
                stats[key] += 1
        if len(updates) >= 20000:
            con.executemany("UPDATE files SET cls=? WHERE rowid=?", updates)
            con.commit()
            updates = []
    con.executemany("UPDATE files SET cls=? WHERE rowid=?", updates)
    con.commit()
    return dict(stats)
