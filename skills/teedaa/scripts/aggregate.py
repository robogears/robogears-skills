#!/usr/bin/env python3
"""teedaa aggregator — turns the classified index into (1) full-scan report
statistics and (2) a pruned treemap payload for the dashboard.

Pruning shapes GEOMETRY ONLY: every statistic (categories, extensions, ages,
top files, junk/dupe totals) is computed from the FULL index before any node
is dropped, and every pruned subtree leaves an exact per-category byte tally
in its rollup leaf, so overlay percentages stay numerically exact.
"""
import os, time
from collections import defaultdict

# Category table (index = catIdx in the payload; ≤12 per digest).
CATEGORIES = ["photos", "screenshots", "images", "videos", "music",
              "documents", "code", "archives", "installers", "junk",
              "cache", "other"]
CAT_IDX = {c: i for i, c in enumerate(CATEGORIES)}
ROLLUP = -1

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tif",
              ".tiff", ".webp", ".bmp", ".ico", ".icns", ".svg", ".psd",
              ".avif", ".jxl", ".xcf"}
# RAW_EXTS must stay identical to photos.py (a drift here mis-categorized
# .srf/.3fr/.rwl inside protected zones) — import the single source of truth.
# IMAGE/VIDEO sets are deliberately NOT shared: photos.py excludes .ico/.svg
# (they are NEVER_PHOTO there) while aggregate counts them as 'images'.
from photos import RAW_EXTS
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".3gp", ".mts", ".m2ts",
              ".mkv", ".webm", ".wmv", ".mpg", ".mpeg", ".insv", ".360",
              ".flv", ".vob"}
MUSIC_EXTS = {".mp3", ".m4a", ".flac", ".wav", ".aiff", ".aif", ".ogg",
              ".aac", ".wma", ".alac", ".opus", ".mid", ".aup3", ".als"}
DOC_EXTS = {".pdf", ".doc", ".docx", ".txt", ".pages", ".xls", ".xlsx",
            ".ppt", ".pptx", ".numbers", ".key", ".rtf", ".md", ".csv",
            ".epub", ".mobi", ".odt", ".ods", ".eml", ".html", ".htm"}
CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".c", ".h", ".cpp", ".cc",
             ".hpp", ".m", ".swift", ".java", ".kt", ".go", ".rs", ".rb",
             ".php", ".cs", ".sh", ".pl", ".lua", ".json", ".yml", ".yaml",
             ".toml", ".xml", ".sql", ".ipynb"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz",
                ".sit", ".sitx", ".iso"}
INSTALLER_EXTS = {".dmg", ".pkg", ".mpkg", ".msi", ".exe", ".deb", ".rpm",
                  ".appimage", ".msix", ".appx"}
GARBAGE_YEARS = {1904, 1970, 1980}

PROT_ZONES = {"git", "project", "bundle", "vm", "db", "iosbackup", "appdata",
              "steam", "library", "homebackup"}
# Only genuine code trees read as "code" on the map; a VM, database, iOS
# backup, or Steam install is categorized by its actual file types instead of
# being blanket-painted "code" (a 60 GB VM reading as code misleads a storage
# audit). (QoL: category_for zone mapping)
CODE_ZONES = {"git", "project", "bundle"}
CACHE_ZONES = {"cache", "rebuildable"}


def _ext(name):
    n = (name or "").lower()
    i = n.rfind(".")
    return n[i:] if i > 0 else ""


def year_of(mtime, this_year):
    if not mtime or mtime <= 0:
        return None
    try:
        y = time.gmtime(mtime).tm_year
    except (OverflowError, OSError, ValueError):
        return None
    if y in GARBAGE_YEARS or y > this_year + 1:
        return None
    return y


def category_for(name, cls, zone_kind):
    """Display category. Sensitive files deliberately keep their underlying
    type — sensitivity is never painted on the map (discreet by design)."""
    if cls:
        k = cls.split(":", 1)[0]
        if k in ("jA", "jB"):
            return CAT_IDX["junk"]
        if cls == "ph:screen":
            return CAT_IDX["screenshots"]
        if cls in ("ph:cert", "ph:likely", "ph:poss", "ph:recv", "ph:side"):
            return CAT_IDX["photos"]
        if cls in ("ph:asset", "ph:unk"):
            return CAT_IDX["images"]
    if zone_kind in CACHE_ZONES:
        return CAT_IDX["cache"]
    if zone_kind in CODE_ZONES:
        return CAT_IDX["code"]
    ext = _ext(name)
    if ext in RAW_EXTS:
        return CAT_IDX["photos"]
    if ext in IMAGE_EXTS:
        return CAT_IDX["images"]
    if ext in VIDEO_EXTS:
        return CAT_IDX["videos"]
    if ext in MUSIC_EXTS:
        return CAT_IDX["music"]
    if ext in DOC_EXTS:
        return CAT_IDX["documents"]
    if ext in CODE_EXTS:
        return CAT_IDX["code"]
    if ext in ARCHIVE_EXTS:
        return CAT_IDX["archives"]
    if ext in INSTALLER_EXTS:
        return CAT_IDX["installers"]
    return CAT_IDX["other"]


class DirAgg:
    __slots__ = ("id", "parent", "name", "zone", "prot", "empty", "status",
                 "children", "files", "total", "cat_bytes", "sub_cats",
                 "junk_bytes", "dupe_bytes", "file_count", "files_direct",
                 "wy_num", "wy_den")

    def __init__(self, id_, parent, name, zone, prot, empty, status):
        self.id = id_
        self.parent = parent
        self.name = name or ""
        self.zone = zone
        self.prot = prot
        self.empty = empty
        self.status = status
        self.children = []
        self.files = []          # kept small: only biggest N retained per dir
        self.total = 0
        self.cat_bytes = None    # DIRECT files' bytes per catIdx (lazy dict)
        self.sub_cats = None     # SUBTREE bytes per catIdx (built bottom-up)
        self.junk_bytes = 0
        self.dupe_bytes = 0
        self.file_count = 0
        self.files_direct = 0
        self.wy_num = 0          # bytes-weighted year accumulator (age lens)
        self.wy_den = 0


def aggregate(con, root, treemap_budget=50000, keep_files_per_dir=50):
    this_year = time.gmtime().tm_year
    nodes = {}
    rootnode = None
    for did, parent, name, zone, prot, empty, status in con.execute(
            "SELECT id, parent, name, zone, prot, empty, status FROM dirs"):
        n = DirAgg(did, parent, name, zone, prot, empty, status)
        nodes[did] = n
    for n in nodes.values():
        if n.parent is None:
            rootnode = n
        else:
            p = nodes.get(n.parent)
            if p:
                p.children.append(n)

    zone_kind_of = {}
    for n in nodes.values():
        if n.prot is not None:
            zone_kind_of[n.id] = nodes[n.prot].zone if n.prot in nodes else None

    totals = {"files": 0, "dirs": len(nodes), "bytes": 0, "physical": 0,
              "dataless_files": 0, "dataless_bytes": 0, "errors": 0}
    cat_totals = [[0, 0] for _ in CATEGORIES]      # [bytes, count]
    ext_totals = defaultdict(lambda: [0, 0])
    age_by_year = defaultdict(lambda: [0, 0])
    top_files = []                                  # (size, ...) kept to 100
    junk = defaultdict(lambda: [0, 0])              # (tier,fam) -> [bytes, n]
    junk_samples = defaultdict(list)
    photo_tiers = defaultdict(lambda: [0, 0])
    sens = defaultdict(lambda: [0, 0])              # (sev,cat) -> [n, bytes]
    sens_dirs = defaultdict(set)
    hash_groups = defaultdict(lambda: [0, 0])       # hash -> [count, size]

    cur = con.execute("SELECT parent, name, size, blocks, mtime, dataless, "
                      "hl_first, cls, hash, src FROM files")
    for parent, name, size, blocks, mtime, dataless, hl_first, cls, h, src \
            in cur:
        node = nodes.get(parent)
        if node is None or name is None:
            continue
        size = size or 0
        counted = size if hl_first else 0
        zk = zone_kind_of.get(parent)
        cat = category_for(name, cls, zk)
        totals["files"] += 1
        totals["bytes"] += counted
        totals["physical"] += (blocks or 0) * 512
        if dataless:
            totals["dataless_files"] += 1
            totals["dataless_bytes"] += counted
        cat_totals[cat][0] += counted
        cat_totals[cat][1] += 1
        e = _ext(name)
        if e:
            ext_totals[e][0] += counted
            ext_totals[e][1] += 1
        y = year_of(mtime, this_year)
        if y:
            age_by_year[y][0] += counted
            age_by_year[y][1] += 1
            node.wy_num += counted * y
            node.wy_den += counted
        node.total += counted
        node.file_count += 1
        node.files_direct += 1
        if node.cat_bytes is None:
            node.cat_bytes = defaultdict(int)
        node.cat_bytes[cat] += counted
        node.files.append((size, name, cat, y, dataless))
        if len(node.files) > keep_files_per_dir * 2:
            node.files.sort(reverse=True)
            del node.files[keep_files_per_dir:]
        if cls:
            k = cls.split(":")
            if k[0] in ("jA", "jB", "jC"):
                key = (k[0][1], k[1])
                junk[key][0] += counted
                junk[key][1] += 1
                if k[0] in ("jA", "jB"):
                    node.junk_bytes += counted
                if len(junk_samples[key]) < 3:
                    junk_samples[key].append(name)
            elif k[0] == "ph":
                photo_tiers[k[1]][0] += counted
                photo_tiers[k[1]][1] += 1
            elif k[0] == "sn":
                sens[(k[1], k[2])][0] += 1
                sens[(k[1], k[2])][1] += counted
                sens_dirs[(k[1], k[2])].add(parent)
        if h and hl_first:
            g = hash_groups[h]
            g[0] += 1
            g[1] = size
        top_files.append((size, parent, name, cat, y, bool(dataless)))
        if len(top_files) > 400:
            top_files.sort(reverse=True)
            del top_files[100:]

    # duplicate overlap (nucleus hashes; placeholder-only by design)
    dupe_groups = {h: g for h, g in hash_groups.items() if g[0] > 1}
    dupe_wasted = sum(size * (n - 1) for n, size in dupe_groups.values())

    # bottom-up dir totals (+ subtree category tallies for rollup honesty)
    order = sorted(nodes.values(), key=lambda n: -_depth(n, nodes))
    for n in order:
        if n.sub_cats is None:
            n.sub_cats = defaultdict(int)
        if n.cat_bytes:
            for c, b in n.cat_bytes.items():
                n.sub_cats[c] += b
        p = nodes.get(n.parent)
        if p is not None:
            p.total += n.total
            p.junk_bytes += n.junk_bytes
            p.file_count += n.file_count
            p.wy_num += n.wy_num
            p.wy_den += n.wy_den
            if p.sub_cats is None:
                p.sub_cats = defaultdict(int)
            for c, b in n.sub_cats.items():
                p.sub_cats[c] += b

    # paths for the pieces that need them
    def path_of(node):
        parts = []
        cur_ = node
        while cur_ is not None and cur_.parent is not None:
            parts.append(cur_.name)
            cur_ = nodes.get(cur_.parent)
        return os.path.join(root, *reversed(parts)) if parts else root

    top_files.sort(reverse=True)
    top100 = [[s, os.path.join(path_of(nodes[p]), nm), c, y or 0, dl]
              for s, p, nm, c, y, dl in top_files[:100] if p in nodes]

    protected = []
    for n in nodes.values():
        if n.zone in PROT_ZONES and n.prot == n.id:
            protected.append({"path": path_of(n), "kind": n.zone,
                              "bytes": n.total, "files": n.file_count})
    protected.sort(key=lambda z: -z["bytes"])
    caches = [{"path": path_of(n), "kind": n.zone, "bytes": n.total,
               "files": n.file_count} for n in nodes.values()
              if n.zone in CACHE_ZONES]
    caches.sort(key=lambda z: -z["bytes"])

    empty_dirs = sum(1 for n in nodes.values() if n.empty)
    reclaim = sorted((n for n in nodes.values() if n.junk_bytes > 0),
                     key=lambda n: -n.junk_bytes)[:20]
    reclaim_rows = [{"path": path_of(n), "junk_bytes": n.junk_bytes}
                    for n in reclaim]

    sens_rows = []
    for (sev, cat), (count, sbytes) in sorted(sens.items()):
        sens_rows.append({"sev": sev, "cat": cat, "count": count,
                          "bytes": sbytes,
                          "dirs": sorted(path_of(nodes[d])
                                         for d in sens_dirs[(sev, cat)]
                                         if d in nodes)[:8]})

    junk_rows = []
    for (tier, fam), (jbytes, count) in sorted(junk.items(),
                                               key=lambda kv: -kv[1][0]):
        junk_rows.append({"tier": tier, "family": fam, "bytes": jbytes,
                          "count": count, "samples": junk_samples[(tier, fam)]})

    tree = _build_treemap(rootnode, nodes, treemap_budget,
                          max(4096, totals["bytes"] // 1_000_000))

    return {
        "root": root, "generated": int(time.time()),
        "totals": totals,
        "cats": CATEGORIES,
        "cat_totals": cat_totals,
        "extTop": sorted(([e, b, c] for e, (b, c) in ext_totals.items()),
                         key=lambda r: -r[1])[:40],
        "ageByYear": {str(y): v for y, v in sorted(age_by_year.items())},
        "top100": top100,
        "junk": junk_rows,
        "photo_tiers": {k: v for k, v in photo_tiers.items()},
        "sensitive": sens_rows,
        "dupes": {"groups": len(dupe_groups), "wasted_bytes": dupe_wasted},
        "protected": protected[:100],
        "caches": caches[:50],
        "empty_dirs": empty_dirs,
        "reclaim": reclaim_rows,
        "tree": tree,
    }


def _depth(n, nodes):
    d = 0
    cur = n
    while cur.parent is not None and cur.parent in nodes:
        d += 1
        cur = nodes[cur.parent]
        if d > 100:
            break
    return d


def _build_treemap(rootnode, nodes, budget, floor):
    """Nested positional arrays. Node discrimination (JS mirrors this):
      catIdx == -1                  -> rollup [label, bytes, -1, [count, tallies]]
      node[3] is a list             -> dir    [name, bytes, cat, children, year]
      else                          -> file   [name, bytes, cat, year]
    year = mtime year (files) / bytes-weighted subtree year (dirs), 0=unknown.
    Children are pre-sorted desc by size (the JS layout never sorts)."""

    def build(node, budget_, depth):
        entries = []
        for c in node.children:
            if c.total > 0 or c.file_count > 0:
                entries.append(("d", c.total, c))
        node.files.sort(reverse=True)
        retained = node.files[:50]
        for size, name, cat, y, _dl in retained:
            entries.append(("f", size, (name, cat, y)))
        entries.sort(key=lambda e: -e[1])
        keep = min(len(entries), max(8, budget_ // 4), 50)
        total = node.total or 1
        kept, pruned_bytes, pruned_count = [], 0, 0
        pruned_cats = defaultdict(int)
        child_budget_pool = max(budget_ - keep, 0)
        kept_bytes = sum(e[1] for e in entries[:keep]) or 1
        retained_cat_bytes = defaultdict(int)
        for size, _name, cat, _y, _dl in retained:
            retained_cat_bytes[cat] += size
        for i, (kind, size, payload) in enumerate(entries):
            significant = size >= 0.005 * total
            if (i < keep or significant) and size >= floor and depth < 12:
                if kind == "d":
                    sub_budget = int(child_budget_pool * (size / kept_bytes))
                    kept.append(build(payload, sub_budget, depth + 1))
                else:
                    name, cat, y = payload
                    kept.append([name, size, cat, y or 0])
            else:
                pruned_bytes += size
                pruned_count += 1
                if kind == "d":
                    src = payload.sub_cats or payload.cat_bytes
                    if src:
                        for cat, b in src.items():
                            pruned_cats[cat] += b
                    else:
                        pruned_cats[CAT_IDX["other"]] += size
                    pruned_count += payload.file_count - 1
                else:
                    pruned_cats[payload[1]] += size
        # Files dropped by the per-dir retention cap never became entries but
        # ARE in node.total — fold the remainder into the rollup so children
        # always sum to the parent and overlay math stays exact.
        unseen = node.files_direct - len(retained)
        if unseen > 0 and node.cat_bytes:
            rem_by_cat = {c: node.cat_bytes.get(c, 0) -
                          retained_cat_bytes.get(c, 0)
                          for c in set(node.cat_bytes) | set(retained_cat_bytes)}
            for c, b in rem_by_cat.items():
                if b > 0:
                    pruned_cats[c] += b
                    pruned_bytes += b
            pruned_count += unseen
        if pruned_count > 0:
            tallies = [pruned_cats.get(i, 0) for i in range(len(CATEGORIES))]
            kept.append([f"({pruned_count:,} more items)", pruned_bytes,
                        ROLLUP, [pruned_count, tallies]])
        year = round(node.wy_num / node.wy_den) if node.wy_den else 0
        return [node.name, node.total, CAT_IDX["other"], kept, year]

    if rootnode is None:
        return ["", 0, CAT_IDX["other"], []]
    return build(rootnode, budget, 0)
