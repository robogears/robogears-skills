#!/usr/bin/env python3
"""teedaa photo discrimination — personal captures vs app assets, by
metadata only (never reads bytes; placeholder-safe by construction).

Returns one of:
  ph:cert    certain personal capture (auto-includable in consolidation)
  ph:likely  likely personal capture
  ph:poss    possible — needs a human eye
  ph:recv    received media (WhatsApp & co) — personal-ish, separate bucket
  ph:screen  screenshot
  ph:asset   app/web asset, icon, stock — NOT a personal photo
  ph:side    sidecar paired with a capture (travels with it atomically)
  ph:unk     image that is dataless with zero signals — defer, never download
  None       not an image/video, or a local image with no signals (plain keep)
"""
import os
import re
import time

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tif",
              ".tiff", ".avif", ".jxl"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".3gp", ".mts", ".m2ts",
              ".mkv", ".webm", ".wmv", ".mpg", ".mpeg", ".insv", ".360"}
RAW_EXTS = {".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".orf",
            ".rw2", ".raf", ".pef", ".dng", ".srw", ".x3f", ".3fr", ".rwl"}
NEVER_PHOTO_EXTS = {".ico", ".icns", ".svg", ".xcf", ".psd", ".webp", ".bmp"}
SIDECAR_EXTS = {".aae", ".xmp", ".thm", ".lrv"}

# Tier A capture-name patterns (anchored to the stem, case-insensitive).
TIER_A = [re.compile(p, re.I) for p in (
    r"^IMG_\d{4}$", r"^IMG_E\d{4}$", r"^_MG_\d{4}$", r"^DSC\d{5}$",
    r"^DSC_\d{4}$", r"^_DSC\d{4}$", r"^DSCN\d{4}$", r"^DSCF\d{4}$",
    r"^CIMG\d{4}$", r"^SANY\d{4}$", r"^IMAG\d{4}$", r"^P\d{7}$",
    r"^SAM_\d{4}$", r"^KIF_\d{4}$", r"^PICT\d{4}$", r"^MVC-?\d{3,5}$",
    r"^1\d{2}_\d{4}$", r"^PXL_\d{8}_\d{9}.*$", r"^IMG_\d{8}_\d{6}.*$",
    r"^VID_\d{8}_\d{6}.*$", r"^MVI_\d{4}$", r"^MOV\d{3,5}$",
    r"^MAH\d{5}$", r"^MAK\d{5}$", r"^GOPR\d{4}$", r"^GP\d{6}$",
    r"^G[HXS]\d{6}$", r"^DJI_\d{4}(_\d{2})?$", r"^DJI_\d{8}_\d{6}_\d+.*$",
)]
SAMSUNG_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})_\d{6}$")
DROPBOX_CAMERA = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2}) \d{2}\.\d{2}\.\d{2}(-\d+| \d+)?$")
TIER_B = [re.compile(p, re.I) for p in (
    r"^Photo \d{4}-\d{2}-\d{2}.*$", r"^Scan ?\d+.*$", r"^scan\d+$",
    r"^IMG_\d{4} \(\d+\)$",
)]

SCREENSHOT_RES = [re.compile(p, re.I) for p in (
    r"^Screen ?Shot \d{4}-\d{2}-\d{2} at \d{1,2}\.\d{2}\.\d{2}( [AP]M)?$",
    r"^Screenshot \d{4}-\d{2}-\d{2} at .*$",
    r"^Screenshot_\d{8}[-_]\d{6}.*$", r"^Screenshot_\d{4}-\d{2}-\d{2}.*$",
    r"^Screenshot \(\d+\)$", r"^Annotation \d{4}-\d{2}-\d{2} \d{6}.*$",
    r"^Screen Recording \d{4}-\d{2}-\d{2}.*$",
)]

RECEIVED_RES = [re.compile(p, re.I) for p in (
    r"^(IMG|VID|AUD|PTT)-\d{8}-WA\d{4}.*$",
    r"^WhatsApp (Image|Video|Audio) \d{4}-\d{2}-\d{2} at .*$",
    r"^FB_IMG_\d{13,}$", r"^photo_\d+@\d{2}-\d{2}-\d{4}.*$",
    r"^signal-\d{4}-\d{2}-\d{2}-\d{6}.*$", r"^Snapchat-\d+$",
    r"^received_\d+$",
)]

WEBJUNK_RES = [re.compile(p, re.I) for p in (
    r"^(download|image|images|unnamed|untitled|photo)( ?\(\d+\))?$",
    r"^[0-9a-f]{32}$", r"^[0-9a-f]{40}$", r"^[0-9a-f]{64}$",
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    r"^\d{9,}$",
    r"^photo-\d{13}-[0-9a-f]{12}$", r"^pexels-photo-\d+.*$",
    r"^istockphoto-\d+-\d+x\d+$", r"^shutterstock_\d+$",
    r"^gettyimages-\d+.*$", r"^adobestock_\d+$",
)]
ICONISH_RE = re.compile(
    r"(?i)(^favicon|^sprite|^glyph|^logo|^btn_|^bg_|^ic_|^asset|"
    r"icon_?\d*x?\d*|@[23]x$|~ipad$)")

ASSET_DIR_TOKENS = {"assets", "static", "img", "textures", "sprites",
                    "drawable", "mipmap", "res", "wp-content", "site-packages",
                    "node_modules", "bower_components", "vendor", "templates",
                    ".git", "resources"}
ASSET_DIR_PREFIXES = ("drawable", "mipmap", "sample", "demo")
PHOTO_DIR_TOKENS = {"camera uploads", "dcim", "camera roll", "pictures",
                    "photos", "photo booth library", "icloud photos",
                    "google photos", "camera", "whatsapp images",
                    "whatsapp video", "takeout"}
APPLE_DCIM_RE = re.compile(r"^1\d\d(APPLE|CANON|NIKON|_PANA|MSDCF|GOPRO)$",
                           re.I)

_GARBAGE_YEARS = {1904, 1970, 1980}


def _valid_year(y):
    try:
        return 1995 <= int(y) <= time.gmtime().tm_year + 1 and \
            int(y) not in _GARBAGE_YEARS
    except (TypeError, ValueError):
        return False


def _ext(name):
    i = name.lower().rfind(".")
    return name.lower()[i:] if i > 0 else ""


def _stem(name):
    e = _ext(name)
    return name[:len(name) - len(e)] if e else name


def _path_tokens(dpath):
    return [c.lower() for c in dpath.split("/") if c]


def _in_asset_zone(toks):
    for t in toks:
        if t in ASSET_DIR_TOKENS or t.startswith(ASSET_DIR_PREFIXES) or \
                t.endswith((".app", ".lproj", ".xcassets", ".imageset",
                            ".iconset", ".bundle", ".framework")):
            return True
    return False


def _in_photo_zone(toks):
    for t in toks:
        if t in PHOTO_DIR_TOKENS or APPLE_DCIM_RE.match(t):
            return True
    return False


def _tier_a_name(stem):
    for rx in TIER_A:
        if rx.match(stem):
            return True
    m = SAMSUNG_DATE.match(stem)
    if m and _valid_year(m.group(1)):
        return True
    return False


def classify_photo(name, size, mtime, dataless, dpath, ctx, root=None):
    # Only consider path components AT OR BELOW the scan root for zone
    # detection — a folder named e.g. "assets" or ".git" ABOVE the root must
    # not flip every image in the tree to asset. Keep the ROOT'S OWN basename
    # though (dropping only what's above it): scanning a folder literally
    # named "Camera Uploads" must still read its top-level files as photos.
    # (audit: absolute-path tokens; reverify: preserve root basename)
    if root and (dpath == root or dpath.startswith(root + "/")):
        above = os.path.dirname(root)
        if dpath == above or dpath.startswith(above + "/"):
            dpath = dpath[len(above):] or "/"
    ext = _ext(name)
    stem = _stem(name)
    is_img = ext in IMAGE_EXTS
    is_vid = ext in VIDEO_EXTS
    is_raw = ext in RAW_EXTS
    if ext in SIDECAR_EXTS:
        # paired sidecar travels with its capture; orphan is stray metadata.
        # Pair against ALL photo/video/RAW extensions — a .xmp beside a Fuji
        # .raf or Olympus .orf is a real pair, not junk. (audit sidecar fix)
        lowstems = {n.lower() for n in ctx.names}
        if any((stem + e).lower() in lowstems
               for e in (IMAGE_EXTS | VIDEO_EXTS | RAW_EXTS)):
            return "ph:side"
        return "jB:SC"
    if ext in NEVER_PHOTO_EXTS:
        return "ph:asset"
    if not (is_img or is_vid or is_raw):
        return None

    toks = _path_tokens(dpath)
    asset_zone = _in_asset_zone(toks)
    photo_zone = _in_photo_zone(toks)

    # Asset zone is a HARD override: app-shipped sample photos have full EXIF
    # and camera-ish names; an image living in an asset tree is an asset.
    if asset_zone:
        return "ph:asset"

    if is_raw:
        return "ph:cert"

    for rx in SCREENSHOT_RES:
        if rx.match(stem):
            return "ph:screen"
    for rx in RECEIVED_RES:
        if rx.match(stem):
            return "ph:recv"
    for rx in WEBJUNK_RES:
        if rx.match(stem):
            return "ph:asset"
    if ICONISH_RE.search(stem):
        return "ph:asset"

    # size floors: cameras never wrote raster files this small — even 1995
    # VGA JPEGs are 20-100 KB. (Skip the check for dataless: size is logical
    # and trustworthy, so DO apply it; None sizes skip.)
    if size is not None and is_img and size < 2048:
        return "ph:asset"
    tiny = size is not None and is_img and size < 10240

    dbx_cam = DROPBOX_CAMERA.match(stem) and _valid_year(stem[:4])
    tier_a = _tier_a_name(stem)

    if dbx_cam:
        return "ph:cert"      # Dropbox itself asserted camera-roll origin
    if tier_a and photo_zone:
        return "ph:cert"
    if ext in (".heic", ".heif"):
        return "ph:cert" if photo_zone else "ph:likely"
    if tier_a:
        return "ph:asset" if tiny else "ph:likely"
    for rx in TIER_B:
        if rx.match(stem):
            return "ph:poss"
    if photo_zone and not tiny:
        return "ph:poss"      # unnamed but living in a camera tree
    if tiny:
        return "ph:asset"
    if dataless:
        return "ph:unk"       # no signals and no bytes: defer, never download
    return None
