#!/usr/bin/env python3
"""AnkiConnect helper + one-time scaffolding for a house-style deck.

Everything configurable lives in config.py. The card design comes from
card_css.py — this file deliberately holds NO copy of it. v1 embedded a stale
pre-redesign template set here and created the note type without the `Hint`
field the templates render and the loader writes, so a by-the-book build shipped
the wrong design with homograph hints silently discarded.
"""
import json
import sys
import urllib.error
import urllib.request

import card_css
from config import (ANKI_KEY, ANKI_URL, DECK, MODEL, TIMEOUT,
                    TIMEOUT_SLOW)

# 11 content fields + Hint. Hint is REQUIRED: card_css.FRONT renders
# {{#Hint}}...{{/Hint}} and add_notes.py writes it for homographs.
FIELDS = ["Hinglish", "English", "Sentence", "SentenceEnglish",
          "WordAudio", "SentenceAudio", "Devanagari",
          "SentenceDevanagari", "Rank", "Notes", "Source", "Hint"]

# Actions whose reported failure is a bare `False` rather than an error string.
_BOOL_RESULT = {"setDeckConfigId", "saveDeckConfig", "removeDeckConfigId"}


class AnkiError(RuntimeError):
    pass


def invoke(action, timeout=None, **params):
    """Call AnkiConnect. Raises AnkiError on any failure it can detect.

    Failure is signalled three different ways by AnkiConnect and v1 noticed only
    the first: an `error` string, a bare `False` result from the deck-config
    actions, and a transport-level exception. All three are errors here.
    """
    payload = {"action": action, "version": 6, "params": params}
    if ANKI_KEY:
        payload["key"] = ANKI_KEY
    req = urllib.request.Request(ANKI_URL, json.dumps(payload).encode())
    try:
        resp = json.load(urllib.request.urlopen(
            req, timeout=timeout or TIMEOUT))
    except urllib.error.URLError as e:
        raise AnkiError(
            f"{action}: cannot reach AnkiConnect at {ANKI_URL} ({e}). "
            "Is Anki open, with the AnkiConnect add-on installed?") from e
    except (OSError, ValueError) as e:
        # urlopen wraps failures that happen while SENDING in URLError, but a
        # read timeout, a reset connection or a truncated body arrives bare —
        # and would sail past every `except AnkiError` in the callers, killing
        # add_notes before it could flush its manifest. That is the exact
        # notes-in-Anki-but-not-in-the-file hazard this whole design exists to
        # prevent, so those failures must land in the same channel.
        raise AnkiError(
            f"{action}: lost the connection to AnkiConnect ({type(e).__name__}: "
            f"{e}). The request may have been applied — run "
            f"`add_notes.py --reconcile` before adding anything else.") from e
    if resp.get("error"):
        raise AnkiError(f"{action}: {resp['error']}")
    result = resp["result"]
    if action in _BOOL_RESULT and result is False:
        raise AnkiError(f"{action}: refused by Anki (returned false)")
    return result


def media_dir():
    """Ask the RUNNING Anki where its media folder is.

    Correct for any profile name. config.MEDIA overrides it only if you set one.
    """
    from config import MEDIA
    return MEDIA or invoke("getMediaDirPath")


def identity():
    """Numeric ids for the deck and note type.

    Names are what the user sees and can rename at any time; ids are stable.
    add_notes.py records these so a later session can notice a rename instead of
    silently creating a second, empty deck under the old name.
    """
    decks = invoke("deckNamesAndIds")
    models = invoke("modelNamesAndIds")
    return {"deck": decks.get(DECK), "model": models.get(MODEL),
            "deck_name": DECK, "model_name": MODEL}


def ensure_model():
    """Create the note type if absent; verify its fields if present."""
    if MODEL not in invoke("modelNames"):
        invoke("createModel", modelName=MODEL, inOrderFields=FIELDS,
               css=card_css.CSS,
               cardTemplates=[{"Name": "Recognition",
                               "Front": card_css.FRONT,
                               "Back": card_css.BACK}])
        print(f"created note type {MODEL!r} with {len(FIELDS)} fields")
        return
    have = invoke("modelFieldNames", modelName=MODEL)
    missing = [f for f in FIELDS if f not in have]
    if missing:
        # AnkiConnect silently DROPS values for field names a model lacks, so an
        # absent Hint would cost every homograph its disambiguator with no error.
        print(f"note type {MODEL!r} is missing fields: {', '.join(missing)}\n"
              f"  add them in Anki (Tools > Manage Note Types > Fields), or run\n"
              f"  invoke('modelFieldAdd', modelName={MODEL!r}, fieldName='Hint')",
              file=sys.stderr)
        raise AnkiError(f"note type {MODEL!r} missing required field(s): {missing}")

    # Matching field NAMES is not enough. An existing note type — the normal
    # case when extending a deck built by an older version — still carries its
    # OLD templates, and those may render the rank suffix themselves. Combined
    # with this version writing the whole phrase into the field, every card
    # would read "147 of 2000 of 2000", and the approved design would silently
    # not apply. card_css.py is the one source of truth, so make that true of
    # the LIVE model too.
    live = invoke("modelTemplates", modelName=MODEL)
    want = {"Recognition": {"Front": card_css.FRONT, "Back": card_css.BACK}}
    name = next(iter(live)) if live else "Recognition"
    current = live.get(name, {})
    if current.get("Front") != card_css.FRONT or current.get("Back") != card_css.BACK:
        invoke("updateModelTemplates",
               model={"name": MODEL,
                      "templates": {name: want["Recognition"]}})
        print(f"note type {MODEL!r}: card templates updated from card_css.py")
    if invoke("modelStyling", modelName=MODEL).get("css") != card_css.CSS:
        invoke("updateModelStyling", model={"name": MODEL, "css": card_css.CSS})
        print(f"note type {MODEL!r}: styling updated from card_css.py")
    print(f"note type {MODEL!r} verified ({len(have)} fields)")


def report_deck_options():
    """Report the deck's options preset. READ-ONLY — never changes anything.

    v3 creates no presets, edits none, and moves no deck between them. This is
    the whole difference from v2, and it is deliberate on three counts:

    * A newly created deck is ALREADY on Anki's Default preset with Default's
      rules — which is exactly "stick with the default settings". Achieving it
      requires calling nothing.
    * Default is SHARED (typically by several decks), so writing perDay onto it
      would silently re-pace every other deck using it. v2 guarded against that
      by minting a per-deck preset instead; but `cloneDeckConfigId` happily
      creates DUPLICATE presets of the same name, so every re-run that found the
      deck on a different preset cloned another one — orphaning a long tail of
      "<deck name> (used by 0 decks)" entries in the options dropdown.
    * An EXISTING deck was put on its preset deliberately. Phase 8 (extend)
      re-runs this scaffold against decks that already have review history, and
      moving one would change its daily limits, learning steps, leech threshold
      and — under FSRS — its optimised parameters, which are stored per preset.

    So: no cloneDeckConfigId, no setDeckConfigId, no saveDeckConfig. There is no
    code path in v3 that can mutate a shared preset. Set daily limits by hand in
    Anki's deck options if you want a different pace.
    """
    try:
        cfg = invoke("getDeckConfig", deck=DECK)
    except AnkiError as e:
        # Reading the options is cosmetic; never let it abort a build that has
        # already created the deck and the note type.
        print(f"could not read deck options ({e}) — continuing; "
              "v3 never changes them anyway", file=sys.stderr)
        return None

    name, cid = cfg.get("name"), cfg.get("id")
    new = (cfg.get("new") or {}).get("perDay")
    rev = (cfg.get("rev") or {}).get("perDay")
    where = "Default preset" if cid == 1 else f"preset {name!r}"
    print(f"deck options: {where} (id {cid}) — left exactly as found "
          f"[{new} new/day, {rev} rev/day]; v3 never creates or edits presets")
    return cfg


def export(path):
    """Write an .apkg backup. Slow for a big deck — hence the longer timeout."""
    invoke("exportPackage", deck=DECK, path=path, includeSched=False,
           timeout=TIMEOUT_SLOW)
    return path


def scaffold():
    print(f"AnkiConnect version {invoke('version')}")
    invoke("createDeck", deck=DECK)
    ensure_model()
    report_deck_options()
    ids = identity()
    print(f"deck {DECK!r} id={ids['deck']}  model {MODEL!r} id={ids['model']}")
    print("scaffold done")


if __name__ == "__main__":
    try:
        scaffold()
    except AnkiError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
