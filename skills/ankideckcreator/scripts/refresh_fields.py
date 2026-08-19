#!/usr/bin/env python3
"""Push corrected content from the batch files onto notes already in Anki.

READ THIS BEFORE RUNNING. This OVERWRITES card content from the batch files. Any
correction you made by hand inside Anki — a fixed gloss, a fixed gender — is
reverted unless it is also in the batch files. v1's docstring claimed it
"rewrites only the two display fields" while overwriting seven, so it read as
safe and silently destroyed hand edits. Use --dry-run first; it reports exactly
which fields would change on how many notes.

Scheduling, note ids and review history are never touched.

Usage:
  refresh_fields.py --dry-run          report what would change (start here)
  refresh_fields.py                    apply
  refresh_fields.py --only Sentence,Hinglish    limit to named fields
"""
import glob
import json
import os
import sys

import record
from anki import AnkiError, invoke
from config import ACCENT_CODE, BASE, MANIFEST, TOTAL, sentence_clip, word_clip
from dedupe import hint_for, same_word
from markup import (escape, normalize_caps, normalize_headword, render_headword,
                    render_sentence)

ACCENT = ACCENT_CODE == "hindi-retroflex"

# Every field this tool can own. Audio references, Rank and Hint are included
# because v1 left them permanently unrepairable: adding or removing a sentence
# after delivery desynced the card from its audio, extending the deck left every
# earlier card showing the old total, and a dedupe improvement never reached the
# live Hint.
REFRESHABLE = ["Hinglish", "Sentence", "English", "Notes", "SentenceEnglish",
               "SentenceDevanagari", "Devanagari", "WordAudio", "SentenceAudio",
               "Rank", "Hint"]


def content_by_rank():
    files = {}
    for tier in ("built", "final"):
        for path in sorted(glob.glob(os.path.join(BASE, tier, "batch_*.json"))):
            files[os.path.basename(path)] = path
    out = {}
    for path in files.values():
        for it in json.load(open(path, encoding="utf-8")):
            # v1 skipped this filter, so content from records the verifier had
            # REJECTED could still be pushed onto live notes.
            if not it.get("keep"):
                continue
            rank = record.rank(it)
            if rank is not None:
                out[rank] = it
    return out


def desired_fields(it, position, hint):
    head = record.get(it, "headword")
    native = record.get(it, "term")
    if ACCENT:
        head = normalize_headword(head, native)
    sent_native = record.get(it, "sent_native")
    sent_display = record.get(it, "sent_display")
    if ACCENT:
        sent_display = normalize_caps(sent_display, sent_native)
    has_sentence = record.has_sentence(it)
    rank = record.rank(it)
    return {
        "Hinglish": render_headword(head, accent=ACCENT),
        "Sentence": render_sentence(sent_display, head, accent=ACCENT) if has_sentence else "",
        "English": escape(record.get(it, "gloss")),
        "Notes": escape(record.get(it, "pos")),
        "SentenceEnglish": escape(record.get(it, "sent_en")) if has_sentence else "",
        "SentenceDevanagari": escape(sent_native) if has_sentence else "",
        "Devanagari": escape(native),
        "WordAudio": f"[sound:{word_clip(rank)}]",
        "SentenceAudio": f"[sound:{sentence_clip(rank)}]" if has_sentence else "",
        "Rank": f"{position} of {TOTAL}" if position else None,
        "Hint": escape(hint),
    }


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    only = None
    if "--only" in args:
        i = args.index("--only") + 1
        if i >= len(args) or args[i].startswith("-"):
            print(f"--only needs a comma-separated field list\nknown: {REFRESHABLE}",
                  file=sys.stderr)
            return 2
        only = [f.strip() for f in args[i].split(",")]
        unknown = [f for f in only if f not in REFRESHABLE]
        if unknown:
            print(f"unknown field(s): {unknown}\nknown: {REFRESHABLE}", file=sys.stderr)
            return 2

    if not os.path.exists(MANIFEST):
        print(f"no manifest at {MANIFEST}. Run `add_notes.py --reconcile` first.",
              file=sys.stderr)
        return 2
    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    notes = manifest["notes"] if isinstance(manifest, dict) else manifest
    content = content_by_rank()

    # Hints depend on the FULL delivered set, not on whichever batches happened
    # to exist when a card was first added — that is how v1 baked a stale
    # hint-less front into an earlier card when its homograph twin arrived later.
    heads = {}
    for it in sorted(content.values(), key=lambda i: record.rank(i) or 0):
        head = normalize_headword(record.get(it, "headword"), record.get(it, "term")) \
            if ACCENT else record.get(it, "headword")
        group = heads.setdefault(head, [])
        # Collapse spelling variants FIRST, exactly as add_notes.load_items does.
        # Grouping without it counts मंदिर/मन्दिर as two cards sharing a headword
        # and pushes a hint onto the one survivor that add_notes correctly left
        # hint-free — the two tools must agree or refresh undoes delivery.
        if not any(same_word(p, it) for p in group):
            group.append(it)
    hints = {}
    for head, group in heads.items():
        for it in group:
            explicit = str(record.get(it, "hint", "")).strip()
            hints[record.rank(it)] = hint_for(it) if (explicit or len(group) > 1) else ""

    live_ids = set(invoke("findNotes", query=f'nid:{",".join(str(m["note_id"]) for m in notes if m.get("note_id"))}')) \
        if any(m.get("note_id") for m in notes) else set()

    updated = skipped = missing = 0
    changes = {}
    for m in notes:
        note_id = m.get("note_id")
        if note_id is None:
            skipped += 1
            continue
        if live_ids and note_id not in live_ids:
            # The user deleted this card in Anki. v1 had no error handling here,
            # so the first deleted note aborted the run and everything after it
            # in the manifest was never refreshed again.
            missing += 1
            continue
        it = content.get(m.get("rank"))
        if not it:
            skipped += 1
            continue
        want = desired_fields(it, m.get("n"), hints.get(m.get("rank"), ""))
        fields = {k: v for k, v in want.items()
                  if v is not None and (only is None or k in only)}
        for k in fields:
            changes[k] = changes.get(k, 0) + 1
        if dry:
            updated += 1
            continue
        try:
            invoke("updateNoteFields", note={"id": note_id, "fields": fields})
            updated += 1
        except AnkiError as e:
            missing += 1
            print(f"  note {note_id} (rank {m.get('rank')}): {e}", file=sys.stderr)
        if updated % 250 == 0:
            print(f"  {updated}", file=sys.stderr)

    verb = "would refresh" if dry else "refreshed"
    print(f"{verb} {updated} note(s); skipped {skipped}; "
          f"missing/failed {missing}")
    print("fields touched: " + ", ".join(f"{k} ({v})" for k, v in sorted(changes.items())))
    if dry:
        print("\nThis OVERWRITES those fields from the batch files. Any edit you "
              "made inside Anki that is not in the batch files will be lost.\n"
              "Re-run without --dry-run to apply, or use --only <fields>.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AnkiError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
