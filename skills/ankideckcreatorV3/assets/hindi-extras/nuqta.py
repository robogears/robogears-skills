#!/usr/bin/env python3
"""Restore nuqta dots so TTS pronounces Perso-Arabic loanwords correctly.

Hindi commonly writes ज़ as ज, फ़ as फ etc. The romanization tells us the true
sound, so where the headword says z/f/q the native form gets its dot back.
Without this the audio says "j"/"ph" while the card says z/f.

    python nuqta.py <dir>            report what would change (safe default)
    python nuqta.py <dir> --write    apply the changes

v1 was a trap in three ways, all of which ended with wrong audio shipping while
the output looked like success: --write was undocumented and the dry run printed
"headwords corrected: N" anyway; the directory was resolved against the SCRIPT's
own folder, so the documented layout scanned nothing and printed "corrected: 0";
and swapped arguments scanned nothing just as quietly. Here the directory is
resolved against the working directory, an empty scan is an error, and a dry run
says so in as many words.
"""
import glob
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "scripts"))
import record  # noqa: E402

NUKTA = "़"
PAIRS = [("z", "ज", "ज़"), ("f", "फ", "फ़"), ("q", "क", "क़")]
DEV_LETTER = re.compile(r"[ऀ-ॣ॰-ॿ]")


def _atomic_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=0)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def count_sound(roman, sound):
    return roman.lower().count(sound)


def fix_word(dev, roman):
    """Return (corrected dev, note). note is None when nothing needed a human."""
    notes = []
    # Normalise first: a precomposed ज़ (U+095B) would otherwise not be seen as
    # already-dotted and could be counted twice.
    out = unicodedata.normalize("NFD", dev)
    out = unicodedata.normalize("NFC", out)
    for sound, plain, dotted in PAIRS:
        n_sound = count_sound(roman, sound)
        n_dotted = out.count(dotted)
        if not n_sound:
            if n_dotted:
                # A dot the romanization does not justify. Never silently
                # removed — that direction needs a human — but no longer
                # invisible either: v1 could not report it at all. Recorded and
                # then CONTINUE, so the remaining letter pairs still get fixed.
                notes.append(f"has {dotted} but romanization has no '{sound}'")
            continue
        n_plain = out.count(plain) - n_dotted
        if n_plain <= 0:
            continue
        remaining = n_sound - n_dotted
        if remaining <= 0:
            # Every sound is already accounted for by an existing dot; adding
            # another would dot a genuine plain letter (v1's n_plain==1 branch).
            continue
        if n_plain != remaining:
            # Only convert when the counts agree exactly. v1 also converted
            # whenever exactly one sound was called for, which picked a letter
            # arbitrarily out of several candidates — silently dotting the wrong
            # one. Ambiguity is for a human to settle.
            notes.append(f"{n_plain} plain {plain} vs {n_sound} '{sound}' — ambiguous")
            continue
        res, converted, i = [], 0, 0
        while i < len(out):
            ch = out[i]
            nxt = out[i + 1] if i + 1 < len(out) else ""
            if ch == plain and nxt != NUKTA and converted < remaining:
                res.append(dotted)
                converted += 1
            else:
                res.append(ch)
            i += 1
        out = "".join(res)
    return out, ("; ".join(notes) if notes else None)


def fix_sentence(sent_native, sent_display):
    """Fix each word of the sentence in place, aligned to the romanization.

    Splitting keeps every token's prefix and suffix where they were. v1 rebuilt
    a token as `fixed + rest`, where `rest` was everything non-Devanagari
    concatenated — so a leading quote migrated to the END of the word, and for a
    hyphenated compound (ज़रा-सा) the core was not even contiguous, the replace
    found nothing, and the whole token got DUPLICATED.
    """
    tokens = re.split(r"(\s+)", sent_native)
    roman_words = sent_display.split()
    dev_idx = [i for i, t in enumerate(tokens) if DEV_LETTER.search(t)]
    if len(dev_idx) != len(roman_words):
        return sent_native, "sentence words do not align with the romanization"
    notes = []
    for idx, roman in zip(dev_idx, roman_words):
        tok = tokens[idx]
        m = re.search(r"[ऀ-ॣ॰-ॿ]+(?:[-‐-―][ऀ-ॣ॰-ॿ]+)*", tok)
        if not m:
            continue
        prefix, core, suffix = tok[:m.start()], m.group(0), tok[m.end():]
        fixed, note = fix_word(core, roman)
        if note:
            notes.append(f"{core}: {note}")
        tokens[idx] = prefix + fixed + suffix
    return "".join(tokens), ("; ".join(notes) if notes else None)


def main():
    args = sys.argv[1:]
    do_write = "--write" in args
    dirs = [a for a in args if not a.startswith("-")]
    if len(dirs) != 1:
        print("usage: nuqta.py <dir> [--write]\n"
              "  <dir> holds the batch json files, relative to where you run "
              "this.\n  Without --write it only reports.", file=sys.stderr)
        return 2
    target = dirs[0]
    if not os.path.isdir(target):
        print(f"not a directory: {target}", file=sys.stderr)
        return 2
    files = sorted(glob.glob(os.path.join(target, "*.json")))
    if not files:
        print(f"no .json files in {target} — nothing to do, and that is "
              f"probably not what you meant", file=sys.stderr)
        return 2

    changed = sentences = 0
    flagged = []
    for path in files:
        data = json.load(open(path, encoding="utf-8"))
        dirty = False
        for it in data:
            tag = f"rank {record.rank(it)}"
            native = record.get(it, "term")
            roman = record.get(it, "headword")
            if not native or not roman:
                continue
            fixed, note = fix_word(native, roman)
            if note:
                flagged.append(f"{tag} {native}: {note}")
            if fixed != native:
                for key in record.ALIASES["term"]:
                    if key in it:
                        it[key] = fixed
                changed += 1
                dirty = True

            sn, sd = record.get(it, "sent_native"), record.get(it, "sent_display")
            if sn and sd:
                # Only the word-by-word pass touches the sentence. v1 ALSO ran a
                # blanket substring replace of the old headword, which rewrote
                # the letters inside unrelated words — fixing जरा corrupted
                # गुजरात into गुज़रात, unrepairably.
                new_sent, note = fix_sentence(sn, sd)
                if note:
                    flagged.append(f"{tag} sentence: {note}")
                if new_sent != sn:
                    for key in record.ALIASES["sent_native"]:
                        if key in it:
                            it[key] = new_sent
                    sentences += 1
                    dirty = True
        if dirty and do_write:
            _atomic_json(path, data)

    mode = "corrected" if do_write else "WOULD correct"
    print(f"{mode}: {changed} headword(s), {sentences} sentence(s) "
          f"across {len(files)} file(s)")
    if flagged:
        print(f"\nneeds a human ({len(flagged)}):")
        for f in flagged[:12]:
            print("   ", f)
        if len(flagged) > 12:
            print(f"    ... and {len(flagged) - 12} more")
    if not do_write:
        print("\nDRY RUN — nothing was written. Re-run with --write to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
