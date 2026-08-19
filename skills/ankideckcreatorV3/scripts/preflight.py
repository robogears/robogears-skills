#!/usr/bin/env python3
"""Check a build's configuration BEFORE it costs anything.

Every failure this catches used to surface only at the Phase-6 final audit —
after thousands of TTS calls, or with a deck already half-delivered. Run it
after editing config.py and before Phase 5.

    python preflight.py
"""
import os
import sys

import card_css
import config


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def main():
    print("config")
    good = True
    good &= check("deck name set", bool(config.DECK), config.DECK)
    good &= check("model name set", bool(config.MODEL), config.MODEL)
    # (v3) No preset check: the skill no longer creates or edits deck presets,
    # so there is no PRESET setting to validate. Deck options are left alone.
    good &= check("media prefix set", bool(config.PREFIX), config.PREFIX)
    good &= check("TOTAL is a positive int",
                  isinstance(config.TOTAL, int) and config.TOTAL > 0, config.TOTAL)
    good &= check("accent code recognised",
                  config.ACCENT_CODE in (None, "hindi-retroflex"),
                  repr(config.ACCENT_CODE))

    print("\ncard templates")
    fields_in_templates = set()
    import re
    for tpl in (card_css.FRONT, card_css.BACK):
        fields_in_templates |= set(re.findall(r"\{\{[#/^]?(\w+)\}\}", tpl))
    fields_in_templates -= {"FrontSide", "Tags", "Type", "Deck", "Subdeck", "Card"}
    from anki import FIELDS
    unknown = sorted(fields_in_templates - set(FIELDS))
    good &= check("every {{Field}} exists in the note type", not unknown,
                  f"unknown: {unknown}" if unknown else "")
    good &= check("Hint field present", "Hint" in FIELDS)
    suffixed = [n for n, t in (("FRONT", card_css.FRONT), ("BACK", card_css.BACK))
                if "of " in t.split("{{Rank}}")[-1][:6]]
    good &= check("rank suffix owned by exactly one side", not suffixed,
                  f"{suffixed} append their own 'of N' — the field already "
                  f"carries the full phrase" if suffixed
                  else "templates render {{Rank}} alone")

    print("\nAnki")
    try:
        from anki import AnkiError, identity, invoke
        ver = invoke("version")
        good &= check("AnkiConnect reachable", True, f"version {ver}")
        media = None
        try:
            from anki import media_dir
            media = media_dir()
        except AnkiError as e:
            good &= check("media folder resolvable", False, str(e))
        if media:
            good &= check("media folder exists", os.path.isdir(media), media)
        ids = identity()
        check("deck exists (created by scaffold if not)", True,
              f"id={ids['deck']}" if ids["deck"] else "not created yet")
        if ids["model"]:
            have = invoke("modelFieldNames", modelName=config.MODEL)
            missing = [f for f in FIELDS if f not in have]
            good &= check("note type has every field", not missing,
                          f"missing: {missing}" if missing else f"{len(have)} fields")
        else:
            check("note type exists", True, "not created yet — run anki.py")
    except Exception as e:                                   # noqa: BLE001
        good &= check("AnkiConnect reachable", False, str(e))
        print("\n    Open Anki and make sure the AnkiConnect add-on is installed.")

    print("\ncontent")
    for tier in ("batches", "built", "final"):
        path = os.path.join(config.BASE, tier)
        n = len([f for f in os.listdir(path) if f.endswith(".json")]) \
            if os.path.isdir(path) else 0
        check(f"{tier}/", True, f"{n} file(s)" if n else "empty")

    print("\nPASS — safe to build" if good else "\nFAIL — fix the above first")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
