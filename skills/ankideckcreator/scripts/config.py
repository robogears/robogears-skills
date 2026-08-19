"""THE one place to configure a deck build. Every other script imports from here.

v1 duplicated DECK/MODEL/PREFIX/TOTAL/VOICE across five files and enforced
agreement with a comment ("must match add_notes.py"). A single mismatched
PREFIX pointed every note at audio that did not exist. There is now one
source of truth; run `python preflight.py` to check it before a build.
"""
import os

# ---------------------------------------------------------------- deck identity
DECK = "Hindi"                 # target deck name in Anki
MODEL = "Hindi Core 2k"        # note type name (reuse across decks of one language)
# No PRESET setting on purpose (v3). This skill never creates, edits or
# reassigns a deck options preset: a new deck lands on Anki's Default with
# Default's rules, an existing deck keeps whatever preset you chose. Set daily
# limits by hand in Anki — Default is shared by several decks, so writing to it
# from here would re-pace all of them. (v2 minted a preset per deck instead,
# which orphaned duplicate presets in the options dropdown on every re-run.)
PREFIX = "hindi2k"             # media filename prefix; must be unique per deck
TAG = "hindi-core2k"           # tag applied to every note this build adds
TOTAL = 2000                   # intended deck size (the "N of TOTAL" denominator)

# ---------------------------------------------------------------- audio
VOICE = "hi-IN-SwaraNeural"    # `edge-tts --list-voices | grep <lang>`
CONCURRENCY = 6                # 4-6 is the tested range
# Anki's media folder. Left None, it is resolved from the running Anki via
# AnkiConnect (getMediaDirPath), which is correct for ANY profile name.
# v1 hardcoded "User 1" and silently wrote thousands of clips into the wrong
# profile when that guess was wrong.
MEDIA = None

# ---------------------------------------------------------------- content rules
# Which language-specific transforms to apply on the way to the card.
#   "hindi-retroflex" — capitals mean retroflex; run normalize_caps /
#                       normalize_headword / mark_retroflex (see references/hindi.md)
#   None              — no accent code: headwords and sentences are shown as written.
# v1 ran the Hindi transforms unconditionally, so an English topic deck got
# "Paris" silently lowercased to "paris".
ACCENT_CODE = "hindi-retroflex"

# ---------------------------------------------------------------- AnkiConnect
ANKI_URL = "http://localhost:8765"
ANKI_KEY = ""                  # set only if you enabled an API key in AnkiConnect
TIMEOUT = 30                   # seconds, per request
TIMEOUT_SLOW = 600             # for exportPackage / big media operations

BASE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(BASE, "manifest.json")
STAMPS = os.path.join(BASE, "audio_stamps.json")


def word_clip(rank):
    """Clip filename for a headword. Keyed by RANK, which never shifts."""
    return f"{PREFIX}_w_r{rank:05d}.mp3"


def sentence_clip(rank):
    return f"{PREFIX}_s_r{rank:05d}.mp3"
