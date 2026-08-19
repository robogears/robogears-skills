#!/usr/bin/env python3
"""teedaa regression tests — every test runs against synthetic fixtures.
REAL Dropbox data is never touched (build rule): the nucleus DB is a
hand-built sqlite file and 'dataless' states are injected into the index.

Run: python3 test_teedaa.py [-v]
"""
import argparse, os, shutil, sqlite3, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import teedaa


# ---------------- synthetic nucleus construction -----------------------------
def _varint(v):
    b = bytearray()
    while True:
        x = v & 0x7F
        v >>= 7
        if v:
            b.append(x | 0x80)
        else:
            b.append(x)
            return bytes(b)


def _tagged_varint(field, v):
    return _varint((field << 3) | 0) + _varint(v)


def _tagged_msg(field, payload):
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def file_metadata(size=None, sha256_hex=None, mtime=None):
    """Protobuf metadata blob for a FILE row (field 4 present)."""
    f4 = b""
    if size:
        f4 += _tagged_varint(2, size)
    if sha256_hex:
        f4 += _tagged_msg(6, _tagged_msg(1, bytes.fromhex(sha256_hex)))
    md = _tagged_msg(4, f4)
    if mtime:
        md += _tagged_varint(13, mtime)
    return md


def dir_metadata(mtime=None):
    """Metadata blob for a DIR row (no field 4)."""
    return _tagged_varint(13, mtime) if mtime else b""


H1 = "aa" * 32
H2 = "bb" * 32
H3 = "cc" * 32
HP = "dd" * 32  # poisoned: appears at two different sizes


def build_nucleus(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE fp_local_tree("
                "itemid INTEGER, dir_itemid INTEGER, filename BLOB, "
                "metadata BLOB, latest INTEGER)")
    con.executemany("INSERT INTO fp_local_tree VALUES(?,?,?,?,1)", rows)
    con.commit()
    con.close()


class Fixture(unittest.TestCase):
    """Shared fixture: a fake Dropbox root + synthetic nucleus DB.

    fs tree (walker-visible):
      dbx/
        cloudy/            <- will be marked status='dataless' (backfill test)
        seen/f_local.bin   <- will be marked dataless=1 (hash-fill test)
        plain.txt
    nucleus tree:
      <root itemid 1, empty name>
        cloudy/  (2)
          f1.bin (3, size 5, H1, mtime 1000000000)
          zero.txt (4, zero-byte: empty field-4 message)
          sub/ (5)
            deep.dat (6, size 7, H2)
          p1.bin (7, size 10, HP)   \\ same hash, two sizes
          p2.bin (8, size 20, HP)   //  -> poisoned sentinel
        seen/ (9)
          f_local.bin (10, size 4, H3)
    """

    def setUp(self):
        teedaa._JSON_ONLY = True    # human lines -> stderr, quiet test output
        self.tmp = tempfile.mkdtemp(prefix="teedaa-test-")
        self.dbx = os.path.join(self.tmp, "dbx")
        os.makedirs(os.path.join(self.dbx, "cloudy"))
        os.makedirs(os.path.join(self.dbx, "seen"))
        with open(os.path.join(self.dbx, "seen", "f_local.bin"), "wb") as f:
            f.write(b"data")
        with open(os.path.join(self.dbx, "plain.txt"), "wb") as f:
            f.write(b"hi")
        self.nucleus = os.path.join(self.tmp, "nucleus.sqlite3")
        build_nucleus(self.nucleus, [
            (1, 1, b"", b""),
            (2, 1, b"cloudy", dir_metadata(999999999)),
            (3, 2, b"f1.bin", file_metadata(5, H1, 1000000000)),
            (4, 2, b"zero.txt", file_metadata()),
            (5, 2, b"sub", dir_metadata()),
            (6, 5, b"deep.dat", file_metadata(7, H2)),
            (7, 2, b"p1.bin", file_metadata(10, HP)),
            (8, 2, b"p2.bin", file_metadata(20, HP)),
            (9, 1, b"seen", dir_metadata()),
            (10, 9, b"f_local.bin", file_metadata(4, H3)),
        ])
        os.environ["TEEDAA_NUCLEUS"] = self.nucleus
        os.environ["TEEDAA_DBX_ROOT"] = self.dbx
        self.index = os.path.join(self.tmp, "index.sqlite3")

    def tearDown(self):
        os.environ.pop("TEEDAA_NUCLEUS", None)
        os.environ.pop("TEEDAA_DBX_ROOT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def scan(self, folder=None, rescan=False):
        ns = argparse.Namespace(folder=folder or self.dbx, json=True,
                                quiet=True, index=self.index, rescan=rescan)
        return teedaa.cmd_scan(ns)

    def con(self):
        return sqlite3.connect(self.index)


class TestProtobuf(unittest.TestCase):
    def test_roundtrip(self):
        md = file_metadata(1234567, H1, 1600000000)
        top = teedaa._fields(md)
        f4 = teedaa._fields(teedaa._first_msg(top, 4))
        self.assertEqual(f4[2][0], 1234567)
        f46 = teedaa._fields(teedaa._first_msg(f4, 6))
        self.assertEqual(f46[1][0].hex(), H1)
        self.assertEqual(top[13][0], 1600000000)

    def test_truncated_never_raises(self):
        md = file_metadata(99, H2)
        for cut in range(len(md)):
            teedaa._fields(md[:cut])   # must not raise

    def test_zero_byte_file(self):
        top = teedaa._fields(file_metadata())
        f4m = teedaa._first_msg(top, 4)
        self.assertIsNotNone(f4m)         # field 4 present -> is a file
        self.assertEqual(teedaa._fields(f4m), {})   # but no size/hash


class TestDropboxDB(Fixture):
    def test_load_and_resolve(self):
        dbx = teedaa.DropboxDB(self.tmp, self.dbx)
        cid = dbx.resolve_dir(os.path.join(self.dbx, "cloudy"))
        self.assertEqual(cid, 2)
        self.assertIsNone(dbx.resolve_dir(os.path.join(self.dbx, "nope")))
        is_file, size, h, mtime = dbx.info[3]
        self.assertTrue(is_file)
        self.assertEqual((size, h, mtime), (5, H1, 1000000000.0))
        is_file, size, h, _ = dbx.info[4]
        self.assertTrue(is_file)          # zero-byte is still a file
        self.assertEqual((size, h), (0, None))
        self.assertFalse(dbx.info[5][0])  # sub is a dir

    def test_poisoned_detection(self):
        dbx = teedaa.DropboxDB(self.tmp, self.dbx)
        self.assertEqual(dbx.poisoned, {HP})
        h, poisoned = dbx.hash_for_child(2, "p1.bin")
        self.assertEqual(h, HP)
        self.assertTrue(poisoned)
        h, poisoned = dbx.hash_for_child(2, "f1.bin")
        self.assertEqual((h, poisoned), (H1, False))


class TestScanAndEnrich(Fixture):
    def test_backfill_dataless_dir(self):
        self.assertEqual(self.scan(), 0)
        con = self.con()
        # inject what a kill-switch EDEADLK would have produced
        con.execute("UPDATE dirs SET status='dataless' WHERE name='cloudy'")
        con.execute("UPDATE meta SET val='0' WHERE key='scan_complete'")
        con.commit()
        stats = teedaa.nucleus_enrich(con, self.dbx)
        self.assertTrue(stats["nucleus_used"])
        self.assertEqual(stats["backfilled_dirs"], 1)    # sub/
        self.assertEqual(stats["backfilled_files"], 5)   # f1 zero p1 p2 deep
        self.assertEqual(stats["poisoned"], 2)           # p1+p2
        rows = {name: (size, h, poisoned) for name, size, h, poisoned in
                con.execute("SELECT name,size,hash,poisoned FROM files "
                            "WHERE src='nucleus'")}
        self.assertEqual(rows["f1.bin"], (5, H1, 0))
        self.assertEqual(rows["zero.txt"], (0, None, 0))
        self.assertEqual(rows["deep.dat"], (7, H2, 0))
        self.assertEqual(rows["p1.bin"][2], 1)   # poisoned: hash refused
        self.assertIsNone(rows["p1.bin"][1])
        # idempotent: enrich again, no duplicates
        stats2 = teedaa.nucleus_enrich(con, self.dbx)
        self.assertEqual(stats2["backfilled_files"], 5)
        n = con.execute("SELECT COUNT(*) FROM files WHERE src='nucleus'"
                        ).fetchone()[0]
        self.assertEqual(n, 5)
        con.close()

    def test_hash_fill_for_placeholder(self):
        self.assertEqual(self.scan(), 0)
        con = self.con()
        con.execute("UPDATE files SET dataless=1 WHERE name='f_local.bin'")
        con.commit()
        stats = teedaa.nucleus_enrich(con, self.dbx)
        self.assertEqual(stats["hashes_filled"], 1)
        h = con.execute("SELECT hash FROM files WHERE name='f_local.bin'"
                        ).fetchone()[0]
        self.assertEqual(h, H3)
        # the materialized file (dataless=0) must stay hashless: stale-DB rule
        h2 = con.execute("SELECT hash FROM files WHERE name='plain.txt'"
                         ).fetchone()[0]
        self.assertIsNone(h2)
        con.close()


class TestClassify(unittest.TestCase):
    """Classifier cascade on a synthetic tree: zones, junk families,
    photos, sensitive — one assertion block per family."""

    @classmethod
    def setUpClass(cls):
        teedaa._JSON_ONLY = True
        cls.tmp = tempfile.mkdtemp(prefix="teedaa-cls-")
        cls.root = os.path.join(cls.tmp, "tree")
        old = 946684800      # 2000-01-01 (vintage)
        y2019 = 1546300800   # 2019-01-01
        fresh = None         # now

        def mk(relpath, content=b"x", mtime=y2019):
            p = os.path.join(cls.root, relpath)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(content)
            if mtime:
                os.utime(p, (mtime, mtime))
            return p

        # protected zones
        mk("repo/.git/HEAD")
        mk("repo/src/main.py")
        mk("repo/junk-in-repo/leftover.log")          # suppressed by zone
        mk("repo/id_rsa", b"k" * 1000)                # cred exact: NOT suppressed
        mk("proj/package.json")
        mk("proj/node_modules/dep/index.js")          # rebuildable
        mk("MyApp.app/Contents/Info.plist")
        mk("backup.sparsebundle/Info.plist")
        os.makedirs(os.path.join(cls.root, "backup.sparsebundle/bands"),
                    exist_ok=True)
        mk("Photos.photoslibrary/database/photos.db")
        # junk families
        mk("dl/.DS_Store")                            # J1 A
        mk("dl/._resource", mtime=y2019)              # J1 C (no sibling)
        mk("dl/movie.mkv.part", mtime=y2019)          # J2 A (stale)
        mk("dl/fresh.zip.crdownload", mtime=fresh)    # J2 skip (fresh)
        mk("dl/archive.part1.rar", mtime=y2019)       # multi-vol: NOT junk
        mk("docs/~$report.docx", mtime=y2019)         # J3 A (stale office lock)
        mk("caches/app.crash")                        # J5 A
        mk("dl/Setup-Thing-2.3.1.dmg", mtime=y2019)   # J6 B (not installed)
        mk("dl/OldApp-1.0.exe", mtime=old)            # J6 vintage -> C
        mk("dl/bundle.zip")                           # J7 C (extracted twin)
        mk("dl/bundle/readme.txt")
        mk("dl/report (1).pdf", content=b"same")      # J8 B (size equals base)
        mk("dl/report.pdf", content=b"same")
        mk("dl/lonely (2).png")                       # J8 C (no base)
        mk("dl/empty.dat", content=b"")               # J9 A
        mk("dl/.gitkeep", content=b"")                # J9 excluded name
        mk("logs/build.log", mtime=y2019)             # J13 B
        mk("logs/icq_2001.log", mtime=old)            # J13 vintage -> C
        # photos
        mk("Camera Uploads/2019-06-01 12.00.00.jpg", b"j" * 50000)  # cert
        mk("random/IMG_2041.JPG", b"j" * 50000)                     # likely
        mk("random/IMG_2041.AAE", b"<plist/>")                      # sidecar
        mk("random/orphan.aae", b"<plist/>")                        # jB:SC
        mk("shots/Screen Shot 2023-01-05 at 1.02.03 PM.png",
           b"p" * 30000)                                            # screen
        mk("wa/IMG-20230101-WA0001.jpg", b"j" * 40000)              # recv
        mk("site/assets/IMG_9999.jpg", b"j" * 40000)  # asset zone overrides
        mk("site/logo@2x.png", b"p" * 30000)          # iconish -> asset
        mk("random/tiny.png", b"p" * 500)             # <2KB -> asset
        mk("random/photo.dng", b"r" * 90000)          # RAW -> cert
        # sensitive
        mk("docs/passport copy.pdf")                  # S1 id
        mk("docs/Emirates ID scan.jpg", b"j" * 50000) # S1 id (bigram)
        mk("docs/bank statement jan.pdf")             # S1 fin
        mk("docs/power of attorney.pdf")              # S1 leg (stopword)
        mk("docs/will.pdf")                           # NOT sensitive (William!)
        mk("docs/goodwill letter.pdf")                # NOT sensitive
        mk("keys/server.pem", b"k" * 800)             # S1 cred by ext
        mk("keys/deck.key", b"k" * 500000)            # Keynote-size: NOT cred
        mk("wallet/wallet.dat")                       # S1 cry
        mk("export/WhatsApp Chat with Mom.txt")       # S1 bak
        mk("docs/tax invoice scan.pdf")               # S2 (single 'tax')
        cls.index = os.path.join(cls.tmp, "index.sqlite3")
        ns = argparse.Namespace(folder=cls.root, json=True, quiet=True,
                                index=cls.index, rescan=False)
        assert teedaa.cmd_scan(ns) == 0
        con = sqlite3.connect(cls.index)
        import classify
        cls.stats = classify.classify_index(con, cls.root)
        cls.cls_by_name = {}
        cls.zone_by_dir = {}
        for name, c in con.execute("SELECT name, cls FROM files"):
            cls.cls_by_name[name] = c
        for name, zone, prot in con.execute(
                "SELECT name, zone, prot FROM dirs"):
            cls.zone_by_dir[name] = (zone, prot)
        con.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def c(self, name):
        return self.cls_by_name.get(name)

    def test_zones(self):
        self.assertEqual(self.zone_by_dir["repo"][0], "git")
        self.assertEqual(self.zone_by_dir["proj"][0], "project")
        self.assertEqual(self.zone_by_dir["MyApp.app"][0], "bundle")
        self.assertEqual(self.zone_by_dir["backup.sparsebundle"][0], "vm")
        self.assertEqual(self.zone_by_dir["Photos.photoslibrary"][0],
                         "library")
        self.assertEqual(self.zone_by_dir["node_modules"][0], "rebuildable")
        # inheritance: src inside repo carries the repo's prot root
        self.assertIsNotNone(self.zone_by_dir["src"][1])

    def test_zone_suppression_and_credential_exception(self):
        self.assertIsNone(self.c("leftover.log"))     # junk suppressed in repo
        self.assertEqual(self.c("id_rsa"), "sn:S1:cred")   # NEVER suppressed

    def test_junk_families(self):
        self.assertEqual(self.c(".DS_Store"), "jA:J1")
        self.assertEqual(self.c("._resource"), "jC:J1")
        self.assertEqual(self.c("movie.mkv.part"), "jA:J2")
        self.assertIsNone(self.c("fresh.zip.crdownload"))
        self.assertIsNone(self.c("archive.part1.rar"))
        self.assertEqual(self.c("~$report.docx"), "jA:J3")
        self.assertEqual(self.c("app.crash"), "jA:J5")
        self.assertEqual(self.c("Setup-Thing-2.3.1.dmg"), "jB:J6")
        self.assertEqual(self.c("OldApp-1.0.exe"), "jC:J6")
        self.assertEqual(self.c("bundle.zip"), "jC:J7")
        self.assertEqual(self.c("report (1).pdf"), "jB:J8")
        self.assertIsNone(self.c("report.pdf"))
        self.assertEqual(self.c("lonely (2).png"), "jC:J8")
        self.assertEqual(self.c("empty.dat"), "jA:J9")
        self.assertIsNone(self.c(".gitkeep"))
        self.assertEqual(self.c("build.log"), "jB:J13")
        self.assertEqual(self.c("icq_2001.log"), "jC:J13")

    def test_photos(self):
        self.assertEqual(self.c("2019-06-01 12.00.00.jpg"), "ph:cert")
        self.assertEqual(self.c("IMG_2041.JPG"), "ph:likely")
        self.assertEqual(self.c("IMG_2041.AAE"), "ph:side")
        self.assertEqual(self.c("orphan.aae"), "jB:SC")
        self.assertEqual(
            self.c("Screen Shot 2023-01-05 at 1.02.03 PM.png"), "ph:screen")
        self.assertEqual(self.c("IMG-20230101-WA0001.jpg"), "ph:recv")
        self.assertEqual(self.c("IMG_9999.jpg"), "ph:asset")
        self.assertEqual(self.c("logo@2x.png"), "ph:asset")
        self.assertEqual(self.c("tiny.png"), "ph:asset")
        self.assertEqual(self.c("photo.dng"), "ph:cert")

    def test_plans(self):
        con = sqlite3.connect(self.index)
        import planner
        plans = {p["type"]: p for p in
                 planner.build_plans(con, self.root, "test")}
        con.close()
        allops = [op for p in plans.values() for op in p["operations"]]
        srcs = [op["src"] for op in allops]
        # rule zero invariants
        for op in allops:
            self.assertTrue(op["src"].startswith(self.root))
            self.assertTrue(op["dst"].startswith(self.root))
            self.assertEqual(op["op"], "rename")
        # sensitive files NEVER planned
        for bad in ("passport copy.pdf", "id_rsa", "server.pem",
                    "wallet.dat", "bank statement jan.pdf"):
            self.assertFalse(any(s.endswith(bad) for s in srcs), bad)
        # protected-zone files never planned
        self.assertFalse(any("/repo/" in s or "/MyApp.app/" in s
                             for s in srcs))
        # C-tier stays report-only
        self.assertFalse(any(s.endswith("bundle.zip") or
                             s.endswith("OldApp-1.0.exe") for s in srcs))
        # junk-safe carries the A-tier certain junk
        safe_srcs = [op["src"] for op in plans["junk-safe"]["operations"]]
        self.assertTrue(any(s.endswith(".DS_Store") for s in safe_srcs))
        self.assertTrue(any(s.endswith("movie.mkv.part") for s in safe_srcs))
        # photo consolidation: capture + its sidecar move together, dated dir
        ph = plans["photo-consolidation"]["operations"]
        img = next(o for o in ph if o["src"].endswith("IMG_2041.JPG"))
        aae = next(o for o in ph if o["src"].endswith("IMG_2041.AAE"))
        self.assertEqual(os.path.dirname(img["dst"]),
                         os.path.dirname(aae["dst"]))
        dbx = next(o for o in ph
                   if o["src"].endswith("2019-06-01 12.00.00.jpg"))
        self.assertTrue(dbx["dst"].endswith("Photos/2019/06/2019-06-01 12.00.00.jpg"))
        # screenshots plan exists and is dated
        sc = plans["screenshots"]["operations"]
        self.assertTrue(sc and "/Screenshots/2023/" in sc[0]["dst"])
        # consent levels present
        for p in plans.values():
            self.assertIn(p["consent"], ("simple", "explicit", "typed"))

    def test_cold_archive_plan(self):
        # a top-level folder untouched for years -> Archive/<year>/; a folder
        # holding a sensitive file is left in place (rule zero)
        con = sqlite3.connect(self.index)
        old = 946684800   # 2000-01-01, ~26 years cold
        # age everything under the vintage 'random' folder tree
        for pth, _d, files in os.walk(os.path.join(self.root, "random")):
            for fn in files:
                os.utime(os.path.join(pth, fn), (old, old))
        # make a top-level folder that holds a sensitive file, also old
        os.makedirs(os.path.join(self.root, "old_taxes"), exist_ok=True)
        with open(os.path.join(self.root, "old_taxes", "bank statement.pdf"),
                  "wb") as f:
            f.write(b"x")
        os.utime(os.path.join(self.root, "old_taxes", "bank statement.pdf"),
                 (old, old))
        con.close()
        # rescan + reclassify so the new files + mtimes are in the index
        ns = argparse.Namespace(folder=self.root, json=True, quiet=True,
                                index=self.index, rescan=True)
        assert teedaa.cmd_scan(ns) == 0
        con = sqlite3.connect(self.index)
        import classify, planner
        classify.classify_index(con, os.path.realpath(self.root))
        plans = {p["type"]: p for p in
                 planner.build_plans(con, os.path.realpath(self.root))}
        con.close()
        self.assertIn("cold-archive", plans)
        arch = plans["cold-archive"]
        dsts = [o["dst"] for o in arch["operations"]]
        # the cold 'random' folder is archived under its last-activity year
        self.assertTrue(any("/Archive/2000/random" in d for d in dsts))
        # the sensitive-holding folder is NEVER moved
        self.assertFalse(any("old_taxes" in o["src"]
                             for o in arch["operations"]))
        self.assertTrue(any("old_taxes" in w for w in arch["warnings"]))

    def test_sensitive(self):
        self.assertEqual(self.c("passport copy.pdf"), "sn:S1:id")
        self.assertEqual(self.c("Emirates ID scan.jpg"), "sn:S1:id")
        self.assertEqual(self.c("bank statement jan.pdf"), "sn:S1:fin")
        self.assertEqual(self.c("power of attorney.pdf"), "sn:S1:leg")
        self.assertIsNone(self.c("will.pdf"))
        self.assertIsNone(self.c("goodwill letter.pdf"))
        self.assertEqual(self.c("server.pem"), "sn:S1:cred")
        self.assertIsNone(self.c("deck.key"))
        self.assertEqual(self.c("wallet.dat"), "sn:S1:cry")
        self.assertEqual(self.c("WhatsApp Chat with Mom.txt"), "sn:S1:bak")
        self.assertEqual(self.c("tax invoice scan.pdf"), "sn:S2:fin")


class TestApplyUndo(unittest.TestCase):
    """Full mutation round-trip on a throwaway fixture: plan -> apply ->
    verify -> undo -> everything back. Plus the refusal gates."""

    def setUp(self):
        teedaa._JSON_ONLY = True
        self.tmp = tempfile.mkdtemp(prefix="teedaa-apply-")
        os.environ["TEEDAA_CACHE_DIR"] = os.path.join(self.tmp, "cache")
        self.root = os.path.join(self.tmp, "tree")
        os.makedirs(os.path.join(self.root, "sub"))
        y2019 = 1546300800
        self.mk(".DS_Store", b"junk")
        self.mk("sub/.DS_Store", b"junk2")
        self.mk("sub/old.mkv.part", b"partial", y2019)
        self.mk("keep.txt", b"keep me")
        self.index = os.path.join(self.tmp, "index.sqlite3")
        ns = argparse.Namespace(folder=self.root, json=True, quiet=True,
                                index=self.index, rescan=False)
        assert teedaa.cmd_scan(ns) == 0
        con = sqlite3.connect(self.index)
        import classify
        classify.classify_index(con, os.path.realpath(self.root))
        import planner
        self.plans = planner.build_plans(con, os.path.realpath(self.root))
        con.close()
        self.safe_plan = next(p for p in self.plans
                              if p["type"] == "junk-safe")
        self.plan_path = os.path.join(self.tmp, "plan.json")
        import json as J
        with open(self.plan_path, "w") as f:
            J.dump(self.safe_plan, f)

    def mk(self, rel, content, mtime=None):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(content)
        if mtime:
            os.utime(p, (mtime, mtime))

    def tearDown(self):
        os.environ.pop("TEEDAA_CACHE_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def apply(self, approve):
        ns = argparse.Namespace(folder=self.root, json=True, quiet=True,
                                index=self.index, plan=self.plan_path,
                                approve=approve)
        return teedaa.cmd_apply(ns)

    def test_gate_refusals(self):
        self.assertEqual(self.apply(None), 3)          # no approval
        self.assertEqual(self.apply("wrong-id"), 3)    # wrong token
        # tampered plan file
        import json as J
        with open(self.plan_path) as f:
            doc = J.load(f)
        doc["operations"][0]["dst"] = "/tmp/evil"
        with open(self.plan_path, "w") as f:
            J.dump(doc, f)
        self.assertEqual(self.apply(self.safe_plan["plan_id"]), 3)
        # nothing moved by any refused attempt
        self.assertTrue(os.path.exists(
            os.path.join(self.root, ".DS_Store")))

    def test_round_trip(self):
        pid = self.safe_plan["plan_id"]
        self.assertEqual(self.apply(pid), 0)
        # junk gone from original spots, present in quarantine
        self.assertFalse(os.path.exists(os.path.join(self.root, ".DS_Store")))
        q = os.path.join(self.root, "_Quarantine", pid)
        self.assertTrue(os.path.exists(os.path.join(q, ".DS_Store")))
        self.assertTrue(os.path.exists(
            os.path.join(q, "sub", "old.mkv.part")))
        # keep.txt untouched
        self.assertTrue(os.path.exists(os.path.join(self.root, "keep.txt")))
        # manifest + undo artifacts
        mdir = os.path.join(self.root, "_teedaa-manifests", pid)
        self.assertTrue(os.path.exists(os.path.join(mdir, "manifest.jsonl")))
        self.assertTrue(os.path.exists(os.path.join(mdir, "undo.sh")))
        self.assertTrue(os.path.exists(os.path.join(mdir, "README.txt")))
        # verify: clean
        ns = argparse.Namespace(folder=self.root, json=True, quiet=True,
                                index=self.index, plan_id=None)
        self.assertEqual(teedaa.cmd_verify(ns), 0)
        # undo: everything home
        self.assertEqual(teedaa.cmd_undo(ns), 0)
        self.assertTrue(os.path.exists(os.path.join(self.root, ".DS_Store")))
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "sub", "old.mkv.part")))
        self.assertFalse(os.path.exists(os.path.join(q, ".DS_Store")))

    def test_symlink_escape_refused(self):
        # a tampered plan whose op path traverses an in-tree symlink to an
        # external dir must be refused by load_plan (audit FS-38603300)
        import json as J
        import executor
        ext = os.path.join(self.tmp, "outside")
        os.makedirs(ext)
        with open(os.path.join(ext, "secret.txt"), "wb") as f:
            f.write(b"secret")
        os.symlink(ext, os.path.join(self.root, "evil"))
        rr = os.path.realpath(self.root)
        plan = {"plan_id": "x", "root": rr, "ops_sha256": None,
                "operations": [{"op": "rename",
                                "src": os.path.join(rr, "evil", "secret.txt"),
                                "dst": os.path.join(rr, "_Q", "secret.txt"),
                                "size": 6}]}
        import planner
        plan["ops_sha256"] = planner.ops_checksum(plan["operations"])
        p, err = executor.load_plan(
            self._write(plan), self.root, "x")
        self.assertIsNone(p)
        self.assertIn("symlink", err)
        self.assertTrue(os.path.exists(os.path.join(ext, "secret.txt")))

    def _write(self, obj):
        import json as J
        path = os.path.join(self.tmp, "tampered.json")
        with open(path, "w") as f:
            J.dump(obj, f)
        return path

    def test_dotdot_path_refused(self):
        import executor, planner
        rr = os.path.realpath(self.root)
        plan = {"plan_id": "x", "root": rr, "ops_sha256": None,
                "operations": [{"op": "rename",
                                "src": os.path.join(rr, "a", "..", "..",
                                                    "escape.txt"),
                                "dst": os.path.join(rr, "q.txt"),
                                "size": 1}]}
        plan["ops_sha256"] = planner.ops_checksum(plan["operations"])
        p, err = executor.load_plan(self._write(plan), self.root, "x")
        self.assertIsNone(p)

    def test_changed_since_plan_is_skipped(self):
        with open(os.path.join(self.root, ".DS_Store"), "ab") as f:
            f.write(b"grew after planning")
        self.assertEqual(self.apply(self.safe_plan["plan_id"]), 0)
        # the changed file was left alone
        self.assertTrue(os.path.exists(os.path.join(self.root, ".DS_Store")))

    def test_failed_rename_does_not_false_alarm_verify(self):
        # a rename failure where src vanished (ENOENT) must not leave a
        # write-ahead 'moved' row that trips a false exit-2 (reverify fix)
        import executor
        pid = self.safe_plan["plan_id"]
        real_rename = os.rename
        victim = os.path.join(self.root, ".DS_Store")

        def flaky_rename(s, d):
            if os.path.realpath(s) == os.path.realpath(victim):
                real_rename(s, s + ".vanished")   # src disappears
                raise OSError(2, "No such file or directory")
            return real_rename(s, d)
        os.rename = flaky_rename
        try:
            self.assertEqual(self.apply(pid), 2)   # apply reports failure
        finally:
            os.rename = real_rename
        # verify must NOT flag the void write-ahead row as data-unaccounted
        ns = argparse.Namespace(folder=self.root, json=True, quiet=True,
                                index=self.index, plan_id=None)
        self.assertEqual(teedaa.cmd_verify(ns), 0)

    def test_verify_flags_unmanifested_quarantine_file(self):
        pid = self.safe_plan["plan_id"]
        self.assertEqual(self.apply(pid), 0)
        # a foreign process (or a crash window) drops a file into quarantine
        # with no manifest row — verify must catch it (QoL orphan detection)
        orphan = os.path.join(self.root, "_Quarantine", pid, "mystery.bin")
        with open(orphan, "wb") as f:
            f.write(b"where did I come from")
        ns = argparse.Namespace(folder=self.root, json=True, quiet=True,
                                index=self.index, plan_id=None)
        self.assertEqual(teedaa.cmd_verify(ns), 2)   # exit 2 = problem found


class TestScanCore(unittest.TestCase):
    def setUp(self):
        teedaa._JSON_ONLY = True
        self.tmp = tempfile.mkdtemp(prefix="teedaa-core-")
        self.root = os.path.join(self.tmp, "tree")
        os.makedirs(os.path.join(self.root, "sub"))
        with open(os.path.join(self.root, "a.txt"), "wb") as f:
            f.write(b"12345")
        with open(os.path.join(self.root, "hl1"), "wb") as f:
            f.write(b"linked")
        os.link(os.path.join(self.root, "hl1"),
                os.path.join(self.root, "sub", "hl2"))
        self.index = os.path.join(self.tmp, "index.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def scan(self):
        ns = argparse.Namespace(folder=self.root, json=True, quiet=True,
                                index=self.index, rescan=False)
        return teedaa.cmd_scan(ns)

    def test_hardlink_counted_once_even_after_resume(self):
        self.assertEqual(self.scan(), 0)
        con = sqlite3.connect(self.index)
        total = con.execute("SELECT SUM(size) FROM files WHERE hl_first=1"
                            ).fetchone()[0]
        self.assertEqual(total, 11)   # 5 + 6, hardlink once
        # simulate a crash that left sub/ pending, then resume
        con.execute("UPDATE meta SET val='0' WHERE key='scan_complete'")
        sid = con.execute("SELECT id FROM dirs WHERE name='sub'").fetchone()[0]
        con.execute("UPDATE dirs SET status='pending' WHERE id=?", (sid,))
        con.execute("DELETE FROM files WHERE parent=?", (sid,))
        con.commit()
        con.close()
        self.assertEqual(self.scan(), 0)
        con = sqlite3.connect(self.index)
        total = con.execute("SELECT SUM(size) FROM files WHERE hl_first=1"
                            ).fetchone()[0]
        self.assertEqual(total, 11)   # regression: was double-counted pre-fix
        firsts = con.execute("SELECT COUNT(*) FROM files WHERE nlink>1 "
                             "AND hl_first=1").fetchone()[0]
        self.assertEqual(firsts, 1)
        con.close()

    def test_rescan_preserves_schema_and_data(self):
        # --rescan must not leave the index looking schema-stale, or the next
        # open_index would wipe the freshly walked data (audit FS-de8a6f23)
        self.assertEqual(self.scan(), 0)
        ns = argparse.Namespace(folder=self.root, json=True, quiet=True,
                                index=self.index, rescan=True)
        self.assertEqual(teedaa.cmd_scan(ns), 0)
        con = sqlite3.connect(self.index)
        self.assertEqual(con.execute("SELECT val FROM meta WHERE key='schema'"
                                     ).fetchone()[0], teedaa.SCHEMA_VERSION)
        n = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        con.close()
        self.assertEqual(n, 3)     # a.txt, hl1, sub/hl2 survived the rescan
        # a plain re-open must NOT drop the data
        con2 = teedaa.open_index(self.index)
        self.assertEqual(con2.execute("SELECT COUNT(*) FROM files"
                                      ).fetchone()[0], 3)
        con2.close()

    def test_refusals(self):
        for target in ("/", "/System", "/Users", "/Volumes",
                       os.path.expanduser("~/Library")):
            ns = argparse.Namespace(folder=target, json=True, quiet=True,
                                    index=self.index, rescan=False)
            self.assertEqual(teedaa.cmd_scan(ns), 3, target)


class TestDatalessSafety(unittest.TestCase):
    """The load-bearing invariant, tested with a FAKE SF_DATALESS flag
    (real placeholders can't be minted in a test — the flag is injected by
    monkeypatching os.scandir's stat results)."""

    def setUp(self):
        teedaa._JSON_ONLY = True
        self.tmp = tempfile.mkdtemp(prefix="teedaa-dl-")
        self.root = os.path.join(self.tmp, "tree")
        os.makedirs(self.root)
        with open(os.path.join(self.root, "cloudy.bin"), "wb") as f:
            f.write(b"x" * 1000)
        with open(os.path.join(self.root, "local.bin"), "wb") as f:
            f.write(b"y" * 500)
        self.index = os.path.join(self.tmp, "index.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sf_dataless_flag_wins(self):
        class FakeStat:
            def __init__(self, st, flags):
                self._st = st
                self.st_flags = flags
            def __getattr__(self, k):
                return getattr(self._st, k)
        real_scandir = os.scandir

        class FakeEntry:
            def __init__(self, e):
                self._e = e
            @property
            def name(self):
                return self._e.name
            @property
            def path(self):
                return self._e.path
            def stat(self, follow_symlinks=True):
                st = self._e.stat(follow_symlinks=follow_symlinks)
                flags = teedaa.SF_DATALESS if self._e.name == "cloudy.bin" \
                    else 0
                return FakeStat(st, flags)

        class FakeScandir:
            # must support the context-manager + iterator protocol, because
            # shutil.rmtree (called from nucleus_enrich's cleanup) uses
            # `with os.scandir(...)` internally
            def __init__(self, path):
                self._it = iter([FakeEntry(e) for e in real_scandir(path)])
            def __iter__(self):
                return self._it
            def __next__(self):
                return next(self._it)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake_scandir(path):
            return FakeScandir(path)
        os.scandir = fake_scandir
        try:
            ns = argparse.Namespace(folder=self.root, json=True, quiet=True,
                                    index=self.index, rescan=False)
            self.assertEqual(teedaa.cmd_scan(ns), 0)
        finally:
            os.scandir = real_scandir
        con = sqlite3.connect(self.index)
        rows = dict(con.execute("SELECT name, dataless FROM files"))
        con.close()
        self.assertEqual(rows["cloudy.bin"], 1)   # flag wins
        self.assertEqual(rows["local.bin"], 0)
        # decmpfs-compressed (UF_COMPRESSED, 0 blocks) must NOT read dataless
        class St:
            st_flags = teedaa.UF_COMPRESSED
            st_blocks = 0
        self.assertFalse(teedaa.st_is_dataless(St()))
        St.st_flags = teedaa.SF_DATALESS | teedaa.UF_COMPRESSED
        self.assertTrue(teedaa.st_is_dataless(St()))


class TestUnicodeAndUndoScript(unittest.TestCase):
    def setUp(self):
        teedaa._JSON_ONLY = True
        self.tmp = tempfile.mkdtemp(prefix="teedaa-uni-")
        os.environ["TEEDAA_CACHE_DIR"] = os.path.join(self.tmp, "cache")
        self.root = os.path.join(self.tmp, "tree")
        os.makedirs(os.path.join(self.root, "دليل عربي"))
        # junk with unicode name + space + quote in path
        p = os.path.join(self.root, "دليل عربي", "it's a Thumbs.db")
        with open(os.path.join(self.root, "دليل عربي", "Thumbs.db"),
                  "wb") as f:
            f.write(b"j")
        with open(p, "wb") as f:
            f.write(b"k")
        self.index = os.path.join(self.tmp, "index.sqlite3")
        ns = argparse.Namespace(folder=self.root, json=True, quiet=True,
                                index=self.index, rescan=False)
        assert teedaa.cmd_scan(ns) == 0
        con = sqlite3.connect(self.index)
        import classify, planner
        classify.classify_index(con, os.path.realpath(self.root))
        self.plans = planner.build_plans(con, os.path.realpath(self.root))
        con.close()

    def tearDown(self):
        os.environ.pop("TEEDAA_CACHE_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unicode_undo_via_shell_script(self):
        plan = next(p for p in self.plans if p["type"] == "junk-safe")
        ppath = os.path.join(self.tmp, "p.json")
        import json as J
        with open(ppath, "w") as f:
            J.dump(plan, f)
        ns = argparse.Namespace(folder=self.root, json=True, quiet=True,
                                index=self.index, plan=ppath,
                                approve=plan["plan_id"])
        self.assertEqual(teedaa.cmd_apply(ns), 0)
        orig = os.path.join(self.root, "دليل عربي", "Thumbs.db")
        self.assertFalse(os.path.exists(orig))
        # restore via the GENERATED SHELL SCRIPT (not the engine)
        import subprocess
        sh = os.path.join(self.root, "_teedaa-manifests", plan["plan_id"],
                          "undo.sh")
        r = subprocess.run(["/bin/sh", sh], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(orig))
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "دليل عربي", "it's a Thumbs.db")))


class TestDashboardXSS(unittest.TestCase):
    """The embedded-JSON breakout defense (audit SEC-9f42da4f)."""

    def test_payload_escapes_all_angle_brackets(self):
        # simulate cmd_report's escaping step
        import json as J
        agg = {"root": "/x", "top100": [
            [1, "</SCRIPT><img src=x onerror=alert(1)>.jpg", 0, 2020, False],
            [2, "<!-- comment.txt", 0, 2020, False]]}
        payload = J.dumps(agg).replace("<", "\\u003c")
        self.assertNotIn("<", payload)          # no raw < survives
        self.assertIn("\\u003c", payload)
        # round-trips back to the exact original once parsed
        self.assertEqual(J.loads(payload)["top100"][0][1],
                         "</SCRIPT><img src=x onerror=alert(1)>.jpg")


class TestSensitiveFPTable(unittest.TestCase):
    """The false-positive table cases that MUST stay negative."""

    def fake_ctx(self, names=()):
        class C:
            pass
        c = C()
        c.names = set(names)
        return c

    def test_never_fire(self):
        import sensitive
        ctx = self.fake_ctx()
        cases = [
            ("will.pdf", 1000),                 # the user is named William
            ("goodwill hunting.mp4", 1000),
            ("Monkey Island key art.png", 1000),
            ("keynote deck.key", 500000),       # Keynote-sized .key
            ("id table schema.sql", 1000),      # code ext gate
            ("passport.js", 1000),              # code ext gate
            ("LICENSE", 1000),                  # bare license, no bigram
            ("env.example", 1000),
            (".env.sample", 1000),
        ]
        for name, size in cases:
            self.assertIsNone(
                sensitive.classify_sensitive(name, size, "/x", ctx),
                name)

    def test_must_fire(self):
        import sensitive
        ctx = self.fake_ctx()
        cases = [
            ("last will and testament.pdf", "sn:S1:leg"),
            ("driving license copy.jpg", "sn:S1:id"),
            ("api key backup.txt", "sn:S1:cred"),
            ("seed phrase.txt", "sn:S1:cry"),
            ("salary certificate 2024.pdf", "sn:S1:fin"),
            ("كشف حساب.pdf", None),   # weak alone — S2 at most, not S1
        ]
        for name, want in cases:
            got = sensitive.classify_sensitive(name, 1000, "/x", ctx)
            if want is None:
                self.assertNotEqual(got, "sn:S1:fin", name)
            else:
                self.assertEqual(got, want, name)

    def test_code_tree_suppression_and_exception(self):
        import sensitive
        ctx = self.fake_ctx()
        self.assertIsNone(sensitive.classify_sensitive(
            "bank statement parser.pdf", 1000, "/x", ctx, in_code_tree=True))
        self.assertEqual(sensitive.classify_sensitive(
            "id_rsa", 1000, "/x", ctx, in_code_tree=True), "sn:S1:cred")

    def test_intl_keywords_reachable(self):
        # combining-mark keywords must match tokenized filenames (audit)
        import sensitive
        ctx = self.fake_ctx()
        self.assertEqual(
            sensitive.classify_sensitive("جواز السفر.pdf", 1000, "/x", ctx),
            "sn:S1:id")
        self.assertEqual(
            sensitive.classify_sensitive("आधार card.pdf", 1000, "/x", ctx),
            "sn:S1:id")

    def test_gcp_regex_not_timestamp(self):
        import sensitive
        ctx = self.fake_ctx()
        # a plain digit timestamp suffix is NOT a GCP key
        self.assertIsNone(sensitive.classify_sensitive(
            "export-202401011200.json", 1000, "/x", ctx))
        # a real hex-suffix service account key IS
        self.assertEqual(sensitive.classify_sensitive(
            "myproj-1a2b3c4d5e6f.json", 1000, "/x", ctx), "sn:S1:cred")


class TestClassifierFixes(unittest.TestCase):
    def ctx(self, names):
        class C:
            pass
        c = C()
        c.names = set(names)
        c.stems = {n.rsplit(".", 1)[0].lower() for n in names}
        c.sizes = {n: 1000 for n in names}
        c.hashes = {}
        c.child_dirnames = set()
        c.lownames = {n.lower() for n in names}
        c.now = __import__("time").time()
        return c

    def test_burst_photo_not_junk(self):
        # "IMG_1234 (1).JPG" reads as a J8 dup-name, but the merge falls
        # through to the photo classifier for image names (audit QUA-a48735b8)
        import classify, photos
        c = self.ctx(["IMG_1234 (1).JPG"])
        self.assertEqual(
            classify.classify_junk("IMG_1234 (1).JPG", 50000, None, 0, None,
                                   c, set()), ("C", "J8"))
        ph = photos.classify_photo("IMG_1234 (1).JPG", 50000, None, 0,
                                   "/Camera Uploads", self.ctx([]))
        self.assertTrue(ph.startswith("ph:") and ph != "ph:asset")

    def test_office_lock_macro_ext(self):
        import classify
        c = self.ctx(["Budget.xlsm"])
        old = __import__("time").time() - 40 * 86400
        self.assertEqual(
            classify.classify_junk("~$Budget.xlsm", 200, old, 0, None, c,
                                   set()), ("A", "J3"))

    def test_ips_rom_patch_not_certain_junk(self):
        import classify
        c = self.ctx([])
        # a ROM-hack .ips patch is review-only, not tier-A
        self.assertEqual(
            classify.classify_junk("MyHack.ips", 5000, None, 0, None, c,
                                   set()), ("C", "J5"))
        # an Apple crash report .ips is tier-A
        self.assertEqual(
            classify.classify_junk("MyApp-2024-01-05-120000.ips", 5000,
                                   None, 0, None, c, set()), ("A", "J5"))

    def test_raw_sidecar_pairs(self):
        import photos
        # .xmp beside a Fuji .raf is a pair, not orphan junk
        c = self.ctx(["shot.raf", "shot.xmp"])
        self.assertEqual(
            photos.classify_photo("shot.xmp", 1000, None, 0, "/x", c),
            "ph:side")

    def test_zone_named_root_keeps_its_basename(self):
        # scanning a folder literally named "Camera Uploads": its top-level
        # photos must still read as photo-zone (reverify fix — root basename
        # was being stripped)
        import photos
        root = "/Users/w/Dropbox/Camera Uploads"
        self.assertEqual(
            photos.classify_photo("IMG_1234.JPG", 50000, None, 0, root,
                                  self.ctx([]), root=root), "ph:cert")
        # a folder ABOVE the root named like a zone must NOT leak in
        root2 = "/Users/w/assets/myphotos"
        got = photos.classify_photo("IMG_1234.JPG", 50000, None, 0, root2,
                                    self.ctx([]), root=root2)
        self.assertNotEqual(got, "ph:asset")   # "assets" ancestor ignored


if __name__ == "__main__":
    unittest.main(verbosity=2)
