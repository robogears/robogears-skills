#!/usr/bin/env python3
"""teedaa sensitive-file detection — METADATA ONLY (names, sizes, paths).
Never reads content; placeholder-safe by construction.

Output: 'sn:S1:<cat>' (definite) / 'sn:S2:<cat>' (borderline — fails closed:
still excluded from every cleanup plan, reported for manual review), or None.
Categories: cred fin id med leg int bak cry.

Mechanics (Purview perf lesson — no big regex wall per file):
lowercase basename once -> tokenize -> O(1) set lookups (exact names,
extensions, single tokens, token bigrams) -> small regex list only on
prefiltered names. Keyword rules are gated to document-ish extensions so
passport.js / LICENSE / contracts.sol never fire. Inside code trees the
keyword categories are suppressed but credential EXACT matches stay live
(an id_rsa in a repo is exactly the thing to flag).
"""
import re
import unicodedata

# --- extension sets ----------------------------------------------------------
CRED_EXTS = {".pem", ".p12", ".pfx", ".pkcs12", ".ppk", ".jks", ".keystore",
             ".bks", ".kdb", ".kdbx", ".agilekeychain", ".opvault",
             ".keychain", ".keychain-db", ".psafe3", ".kwallet", ".1pif",
             ".enpassbackup", ".ovpn", ".tblk", ".rdp", ".p8", ".gpg",
             ".pgp"}
FIN_EXTS = {".qdf", ".qbb", ".qbw", ".qba", ".qbo", ".qif", ".ofx", ".qfx",
            ".mny", ".gnucash"}
TAX_EXT_RE = re.compile(r"^\.tax\d{2,4}$|^\.t\d{2}$")
MED_EXTS = {".dcm", ".dicom", ".dic", ".hl7", ".cda", ".ccd", ".ccda"}
BAK_EXTS = {".pst", ".ost", ".olm", ".mbox"}
DOCISH_EXTS = {".pdf", ".doc", ".docx", ".pages", ".jpg", ".jpeg", ".png",
               ".heic", ".heif", ".tif", ".tiff", ".webp", ".bmp", ".txt",
               ".rtf", ".zip", ".xls", ".xlsx", ".numbers", ".csv", ".eml",
               ".mbox", ""}

# --- exact names (case-insensitive) -----------------------------------------
CRED_EXACT = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "otr.private_key",
              "secring.gpg", "pubring.gpg", "trustdb.gpg", "credentials.json",
              "credentials.db", "credentials.xml", "adc.json", ".htpasswd",
              ".netrc", "_netrc", ".pgpass", ".git-credentials", ".npmrc",
              ".pypirc", ".boto", ".s3cfg", ".dockercfg", ".mysql_history",
              ".psql_history", ".irb_history", "terraform.tfvars",
              "terraform.tfstate", "terraform.tfstate.backup",
              "sftp-config.json", "filezilla.xml", "recentservers.xml",
              "logins.json", "key4.db", "login data", "login.keychain",
              "login.keychain-db", "passwords.csv", "database.yml",
              "settings.py.bak", ".pgpass.conf", "proftpdpasswd", "knife.rb",
              "secret_token.rb", "shadow", "htpasswd", "kwallet.kwl"}
CRED_EXACT_RES = [re.compile(p, re.I) for p in (
    r"^\.env(\..+)?$", r".*_(rsa|dsa|ed25519|ecdsa)$",
    r"^authkey_[a-z0-9]{10}\.p8$", r".*_passwords?\.csv$",
    r"^bitwarden_export.*\.json$", r"^lastpass_export.*\.csv$",
    r"^(\.)?(bash_|zsh_|sh_|z)?history$",
    r"^service[-_]?account.*\.json$",
    # GCP service-account key: 12-hex suffix, but require >=1 letter so a
    # plain "-202401011200.json" timestamp export isn't flagged. (audit)
    r".*-(?=[a-f0-9]{12}\.json$)[a-f0-9]*[a-f][a-f0-9]*\.json$",
)]
ENV_FALSE = re.compile(r"\.(example|sample|template|dist)$", re.I)
CRY_EXACT = {"wallet.dat", "electrum.dat", "seed.txt"}
CRY_RES = [re.compile(p, re.I) for p in (
    r"^utc--\d{4}-\d{2}-\d{2}t.*--[0-9a-f]{40}$", r".*\.wallet$",
)]
BAK_EXACT = {"_chat.txt", "wa.db", "msgstore.db"}
BAK_RES = [re.compile(p, re.I) for p in (
    r"^whatsapp chat with .*\.(txt|zip)$", r"^msgstore.*\.crypt\d+$",
    r"^signal-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.backup$",
    r"^takeout-\d{8}t\d{6}z-\d+\.(zip|tgz)$",
    r"^genome_.*_v\d+.*\.txt$", r"^ancestrydna\.txt$",
)]
MED_EXACT = {"dicomdir"}

# --- keyword tables (single tokens & bigrams after tokenization) -------------
S1_SINGLES = {"ssn": "id", "payslip": "fin", "prescription": "med",
              "ejari": "leg", "iban": "fin", "aadhaar": "id", "aadhar": "id",
              "iqama": "id", "kyc": "id", "mrz": "id"}
S2_SINGLES = {"passport": "id", "visa": "id", "eid": "id", "noc": "leg",
              "mnemonic": "cry", "statement": "fin", "invoice": "fin",
              "bank": "fin", "tax": "fin", "salary": "fin", "medical": "med",
              "poa": "leg", "nda": "leg", "attested": "id", "mohre": "id",
              "private": "int", "vault": "int"}
S1_BIGRAMS = {
    # identity
    ("passport", "copy"): "id", ("passport", "scan"): "id",
    ("passport", "photo"): "id", ("passport", "page"): "id",
    ("national", "id"): "id", ("id", "card"): "id",
    ("identity", "card"): "id", ("emirates", "id"): "id",
    ("eid", "copy"): "id", ("residence", "visa"): "id",
    ("visit", "visa"): "id", ("entry", "permit"): "id",
    ("residence", "permit"): "id", ("driving", "license"): "id",
    ("driving", "licence"): "id", ("drivers", "license"): "id",
    ("driver", "license"): "id", ("labour", "card"): "id",
    ("labor", "card"): "id", ("green", "card"): "id",
    ("birth", "certificate"): "id", ("marriage", "certificate"): "id",
    ("family", "book"): "id", ("pan", "card"): "id", ("voter", "id"): "id",
    ("social", "security"): "id", ("visa", "page"): "id",
    ("oci", "card"): "id", ("salary", "certificate"): "fin",
    # financial
    ("bank", "statement"): "fin", ("account", "statement"): "fin",
    ("credit", "card"): "fin", ("card", "statement"): "fin",
    ("salary", "slip"): "fin", ("salary", "transfer"): "fin",
    ("tax", "return"): "fin", ("w2", "form"): "fin", ("form", "1099"): "fin",
    ("swift", "code"): "fin", ("sort", "code"): "fin",
    ("routing", "number"): "fin", ("bank", "details"): "fin",
    ("loan", "agreement"): "fin",
    ("cheque", "copy"): "fin", ("bank", "account"): "fin",
    # medical
    ("medical", "report"): "med", ("medical", "record"): "med",
    ("medical", "history"): "med", ("lab", "report"): "med",
    ("lab", "results"): "med", ("blood", "test"): "med",
    ("test", "results"): "med", ("x", "ray"): "med", ("xray", "report"): "med",
    ("ct", "scan"): "med", ("mri", "scan"): "med", ("biopsy", "report"): "med",
    ("discharge", "summary"): "med", ("covid", "test"): "med",
    ("pcr", "result"): "med", ("insurance", "claim"): "med",
    ("insurance", "card"): "med", ("sick", "leave"): "med",
    ("mental", "health"): "med", ("therapy", "notes"): "med",
    ("vaccination", "certificate"): "med", ("vaccine", "certificate"): "med",
    ("fitness", "certificate"): "med",
    # legal
    ("last", "will"): "leg", ("living", "will"): "leg",
    ("will", "testament"): "leg",
    ("power", "attorney"): "leg", ("non", "disclosure"): "leg",
    ("tenancy", "contract"): "leg", ("title", "deed"): "leg",
    ("court", "order"): "leg", ("court", "case"): "leg",
    ("divorce", "papers"): "leg", ("custody", "agreement"): "leg",
    ("settlement", "agreement"): "leg", ("employment", "contract"): "leg",
    ("labour", "contract"): "leg", ("labor", "contract"): "leg",
    ("trade", "license"): "leg", ("trade", "licence"): "leg",
    ("memorandum", "association"): "leg",
    # credentials-ish phrases
    ("api", "key"): "cred", ("private", "key"): "cred",
    ("ssh", "key"): "cred", ("seed", "phrase"): "cry",
    ("recovery", "phrase"): "cry", ("secret", "phrase"): "cry",
    ("mnemonic", "phrase"): "cry", ("backup", "phrase"): "cry",
    ("recovery", "seed"): "cry", ("12", "words"): "cry",
    ("24", "words"): "cry",
}
BANK_NAMES = {"enbd", "adcb", "fab", "hsbc", "citibank", "chase", "barclays",
              "rakbank", "mashreq", "emirates"}
INTIMATE_DIR_S1 = {"nudes", "nsfw", "onlyfans", "boudoir"}
INTIMATE_DIR_S2 = {"private", "personal", "hidden", "vault", "secret"}
# Arabic / multilingual whole tokens (kept small + high-precision)
INTL_S1 = {"جواز": "id", "تأشيرة": "id", "الهوية": "id", "إقامة": "id",
           "passeport": "id", "reisepass": "id", "personalausweis": "id",
           "pasaporte": "id", "passaporto": "id", "паспорт": "id",
           "पासपोर्ट": "id", "پاسپورٹ": "id", "आधार": "id",
           "kontoauszug": "fin", "gehaltsabrechnung": "fin",
           "راتب": "fin", "فاتورة": "fin", "ضريبة": "fin",
           "ordonnance": "med", "befund": "med", "arztbrief": "med",
           "وصفة": "med", "تقرير": None}

_TOKEN_RE = re.compile(r"[^a-z0-9؀-ۿЀ-ӿऀ-ॿ]+")
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
# dropped before bigram formation so "power of attorney" and "last will and
# testament" still form their signature adjacent pairs
STOPWORDS = {"of", "the", "and", "a", "an", "my", "de", "du", "la", "el"}


def _fold(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def tokenize(name):
    base = _CAMEL_RE.sub(" ", name)
    base = _fold(base).lower()
    return [t for t in _TOKEN_RE.split(base) if t and t not in STOPWORDS]


# Keyword dictionaries must be folded through the SAME pipeline as filenames,
# or keys carrying combining marks (Arabic hamza, Devanagari virama, accents)
# can never match a tokenized name. (audit: unreachable INTL keywords)
def _fold_key(k):
    return _fold(k).lower()


INTL_S1 = {_fold_key(k): v for k, v in INTL_S1.items()}


def _ext(name):
    i = name.lower().rfind(".")
    return name.lower()[i:] if i > 0 else ""


def _sibling_has(ctx, *exts):
    for n in ctx.names:
        if _ext(n) in exts:
            return True
    return False


def classify_sensitive(name, size, dpath, ctx, in_code_tree=False):
    low = name.lower()
    ext = _ext(name)
    stem = low[:len(low) - len(ext)] if ext else low

    # ---- credential exacts & extensions: NEVER suppressed ------------------
    if low in CRED_EXACT:
        return "sn:S1:cred"
    for rx in CRED_EXACT_RES:
        if rx.match(low) and not ENV_FALSE.search(low):
            # bare keyfile heuristic: real private keys are 200 B – 20 KB
            if rx.pattern.endswith("(rsa|dsa|ed25519|ecdsa)$") and size and \
                    not (200 <= size <= 20480):
                return "sn:S2:cred"
            return "sn:S1:cred"
    if ext in CRED_EXTS:
        return "sn:S1:cred"
    if ext == ".key":
        # Keynote trap: .key >200 KB (or a dir-bundle, handled by zones) is a
        # presentation; a small .key is very likely a private key.
        if size is not None and size > 200 * 1024:
            return None
        return "sn:S1:cred"
    if ext == ".asc":
        return "sn:S2:cred" if (size or 0) < 20480 else None
    if low in CRY_EXACT:
        return "sn:S1:cry"
    for rx in CRY_RES:
        if rx.match(low):
            return "sn:S1:cry"

    # ---- backup / export shapes (structure beats keywords) -----------------
    if low in BAK_EXACT or ext in BAK_EXTS:
        return "sn:S1:bak"
    for rx in BAK_RES:
        if rx.match(low):
            return "sn:S1:bak"
    if ext == ".vcf":
        if low.startswith("contacts") or (size or 0) < 5 * 1024 * 1024:
            return "sn:S2:bak"
        return "sn:S1:med"    # large .vcf = genomic variant file
    if ext in MED_EXTS or low in MED_EXACT:
        return "sn:S1:med"
    if ext in FIN_EXTS or TAX_EXT_RE.match(ext or ""):
        return "sn:S1:fin"

    # ---- intimate media: folder-name signal, discreet folder-level ---------
    dtoks = set(tokenize(dpath.rsplit("/", 1)[-1]))
    if dtoks & INTIMATE_DIR_S1:
        return "sn:S1:int"
    if dtoks & INTIMATE_DIR_S2 and ext in (".jpg", ".jpeg", ".png", ".heic",
                                           ".mp4", ".mov"):
        return "sn:S2:int"

    # ---- keyword categories: doc-ish extensions only, code trees suppressed
    if in_code_tree or ext not in DOCISH_EXTS:
        return None
    toks = tokenize(stem)
    if not toks:
        return None
    tokset = set(toks)

    # FP table hard rules
    #  - 'will' NEVER alone (the user is literally named William)
    #  - id/dl/ct/mr/rx/sin never alone; license needs a qualifying bigram
    #  - 'contract' suppressed when siblings scream Solidity
    if "contract" in tokset and _sibling_has(ctx, ".sol"):
        return None

    for i in range(len(toks) - 1):
        bg = (toks[i], toks[i + 1])
        cat = S1_BIGRAMS.get(bg)
        if cat:
            return f"sn:S1:{cat}"
    if tokset & BANK_NAMES and ("statement" in tokset or "account" in tokset):
        return "sn:S1:fin"
    for t in toks:
        cat = S1_SINGLES.get(t)
        if cat:
            return f"sn:S1:{cat}"
        cat = INTL_S1.get(t)
        if cat:
            return f"sn:S1:{cat}"
    for t in toks:
        cat = S2_SINGLES.get(t)
        if cat:
            # eid burst-check: an 'eid' photo among a big same-extension pile
            # is far more likely Eid holiday pictures than an ID scan
            if t == "eid" and ext in (".jpg", ".jpeg", ".heic", ".png") and \
                    sum(1 for n in ctx.names if _ext(n) == ext) > 20:
                return None
            return f"sn:S2:{cat}"
    return None
