#!/usr/bin/env python3
"""Load verified content into Anki via AnkiConnect.

Incremental and genuinely idempotent. Three v1 failures are fixed here, and they
compounded into the worst outcome the whole skill could produce — hundreds of
duplicate notes in a live collection:

  * the manifest was written ONCE, after every chunk had been sent, so any
    mid-run failure left notes in Anki that no file recorded;
  * every note carried allowDuplicate:true, disabling Anki's own last-ditch
    duplicate check;
  * so the re-run the docstring invited re-added everything already delivered.

Now the manifest is flushed atomically after every chunk, duplicates are NOT
allowed, and `--reconcile` rebuilds the manifest from the live deck when it is
lost or stale.

Usage:
  add_notes.py                 add whatever is not yet delivered
  add_notes.py --dry-run       show what would be added, touch nothing
  add_notes.py --reconcile     rebuild manifest from the live deck, then stop
  add_notes.py --allow-unordered   proceed even if a late batch breaks rank order
"""
import glob
import json
import os
import sys

import record
from anki import AnkiError, identity, invoke
from config import (ACCENT_CODE, BASE, DECK, MANIFEST, MODEL, TAG, TOTAL,
                    sentence_clip, word_clip)
from dedupe import hint_for, hints_distinct, same_word
from markup import (escape, normalize_caps, normalize_headword, render_headword,
                    render_sentence)

ACCENT = ACCENT_CODE == "hindi-retroflex"


def _atomic_json(path, data):
    """Write via a temp file + rename, so a crash cannot truncate the original.

    v1 wrote every state file with a plain open(path, "w"), which empties the
    file before the new content lands. A truncated manifest is the single worst
    thing that can happen here: the next run crashes reading it, and the natural
    reflex — delete it — is exactly what triggers mass re-adding.
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def source_files():
    """Verified content wins over builder output, per batch."""
    files = {}
    for tier in ("built", "final"):
        for path in sorted(glob.glob(os.path.join(BASE, tier, "batch_*.json"))):
            files[os.path.basename(path)] = path
    return [files[k] for k in sorted(files)]


def load_items(verbose=True):
    """Kept items in rank order, spelling-variant duplicates collapsed."""
    items = []
    unverified = []
    final_names = {os.path.basename(p)
                   for p in glob.glob(os.path.join(BASE, "final", "batch_*.json"))}
    for path in source_files():
        if os.path.basename(path) not in final_names:
            unverified.append(os.path.basename(path))
        items.extend(json.load(open(path, encoding="utf-8")))
    if unverified and verbose:
        # v1 fell back to unverified builder output silently, and because a
        # delivered rank is never revisited, that content became permanent even
        # after the verifier corrected or rejected it.
        print(f"WARNING: {len(unverified)} batch(es) have no verified final/ "
              f"file yet: {', '.join(sorted(unverified)[:5])}"
              f"{' ...' if len(unverified) > 5 else ''}\n"
              f"  Content added from built/ is NOT re-read later. Prefer waiting "
              f"for verification, or re-run refresh_fields.py afterwards.",
              file=sys.stderr)

    kept = [i for i in items if i.get("keep")]
    # A record with no rank cannot be delivered at all: audio filenames key on
    # the rank, and so does resume. Drop it here with a warning rather than let
    # it crash note-building halfway through a chunk. lint.py reports these as
    # missing_rank.
    unranked = [i for i in kept if record.rank(i) is None]
    if unranked:
        kept = [i for i in kept if record.rank(i) is not None]
        if verbose:
            print(f"WARNING: skipping {len(unranked)} record(s) with no rank "
                  f"(e.g. {record.get(unranked[0], 'headword')!r}). Run lint.py "
                  f"to find them.", file=sys.stderr)
    kept.sort(key=record.rank)

    by_head, out, collapsed = {}, [], []
    for it in kept:
        head = normalize_headword(record.get(it, "headword"),
                                  record.get(it, "term")) if ACCENT \
            else record.get(it, "headword")
        group = by_head.setdefault(head, [])
        dup = next((p for p in group if same_word(p, it)), None)
        if dup is not None:
            # v1 dropped these with a bare `continue` and no record at all,
            # despite the pipeline's own "rejects stay in the file with a
            # reason" principle — so a misclassification was invisible.
            collapsed.append((record.rank(it), record.get(it, "term"),
                              record.get(dup, "term")))
            continue
        group.append(it)
        out.append(it)

    for head, group in by_head.items():
        for it in group:
            # An explicit `hint` is a documented field, so it ships whether or
            # not the headword collides — a builder uses it to separate a word
            # from something OUTSIDE this deck. A hint DERIVED from pos is only
            # meaningful against a collision, so that one stays group-gated.
            explicit = str(record.get(it, "hint", "")).strip()
            if explicit or len(group) > 1:
                it["_hint"] = hint_for(it)
            # Separate from the hint: this marks a card whose FIRST FIELD really
            # does collide inside this deck, which is the only reason to let it
            # past Anki's duplicate check.
            it["_homograph"] = len(group) > 1
        if len(group) > 1:
            if not hints_distinct(group) and verbose:
                terms = ", ".join(record.get(i, "term") for i in group)
                print(f"WARNING: homographs {head!r} ({terms}) share the hint "
                      f"{hint_for(group[0])!r} — their fronts are identical and "
                      f"the learner cannot tell which is being asked. Give each "
                      f"a distinct `pos` (gender!) or an explicit `hint`.",
                      file=sys.stderr)
    if collapsed and verbose:
        print(f"collapsed {len(collapsed)} spelling variant(s):", file=sys.stderr)
        for rk, term, into in collapsed[:8]:
            print(f"    rank {rk} {term} -> {into}", file=sys.stderr)
        _atomic_json(os.path.join(BASE, "collapsed.json"),
                     [{"rank": r, "term": t, "merged_into": i}
                      for r, t, i in collapsed])
    return out


def to_note(item, position):
    """Build one AnkiConnect note payload from a record.

    Every text value is HTML-escaped on the way in (markup.py does it as part of
    rendering). v1 passed Tatoeba text through verbatim, so community-authored
    content reached Anki's renderer as live HTML.
    """
    head = record.get(item, "headword")
    native = record.get(item, "term")
    if ACCENT:
        head = normalize_headword(head, native)
    sent_native = record.get(item, "sent_native")
    sent_display = record.get(item, "sent_display")
    if ACCENT:
        sent_display = normalize_caps(sent_display, sent_native)
    has_sentence = record.has_sentence(item)
    sent_html = render_sentence(sent_display, head, accent=ACCENT) if has_sentence else ""

    src = record.get(item, "source_id")
    source = f"Tatoeba #{src} (CC-BY 2.0 FR)" if src else "written for this deck"
    rank = record.rank(item)

    return {
        "deckName": DECK,
        "modelName": MODEL,
        "fields": {
            "Hinglish": render_headword(head, accent=ACCENT),
            "English": escape(record.get(item, "gloss")),
            "Sentence": sent_html,
            "SentenceEnglish": escape(record.get(item, "sent_en")) if has_sentence else "",
            # keyed by rank, which never shifts, so audio survives any
            # renumbering of deck positions
            "WordAudio": f"[sound:{word_clip(rank)}]",
            # audio.py generates the sentence clip on exactly this condition, so
            # the reference and the file can never disagree (v1 keyed the two on
            # different fields and shipped play buttons pointing at nothing)
            "SentenceAudio": f"[sound:{sentence_clip(rank)}]" if has_sentence else "",
            "Devanagari": escape(native),
            "SentenceDevanagari": escape(sent_native) if has_sentence else "",
            # the template renders {{Rank}} alone; the whole phrase lives here
            "Rank": f"{position} of {TOTAL}",
            "Notes": escape(record.get(item, "pos")),
            "Source": escape(source),
            "Hint": escape(item.get("_hint", "")),
        },
        # Anki's duplicate check compares the FIRST field, so it is the last
        # thing standing between a desynced manifest and a polluted collection
        # — v1 disabled it outright and paid for it. But genuine homographs
        # (की/कि, both "ki") legitimately share that field, and refusing them
        # would break the feature this deck is built around. So the exemption is
        # exactly the cards we deliberately marked as homographs, and nothing
        # else: the cards whose headword genuinely collides in this deck.
        "options": {"allowDuplicate": bool(item.get("_homograph")),
                    "duplicateScope": "deck"},
        "tags": [TAG],
    }


def load_manifest():
    if not os.path.exists(MANIFEST):
        return {"deck": None, "model": None, "notes": []}
    data = json.load(open(MANIFEST, encoding="utf-8"))
    if isinstance(data, list):          # v1 format: a bare list of note entries
        return {"deck": None, "model": None, "notes": data}
    return data


def reconcile():
    """Rebuild the manifest from the LIVE deck.

    The deck in Anki is the truth; the manifest is a cache. This makes a lost or
    stale manifest a non-event instead of the trigger for mass duplication, and
    it is what makes extending a deck weeks later (in a fresh scratchpad with no
    state files) safe.
    """
    note_ids = invoke("findNotes", query=f'deck:"{DECK}" tag:{TAG}')
    if not note_ids:
        print(f"no notes tagged {TAG} in deck {DECK!r}")
        return {"deck": None, "model": None, "notes": []}
    info = invoke("notesInfo", notes=note_ids)
    notes = []
    for n in info:
        fields = n.get("fields", {})
        rank_text = fields.get("Rank", {}).get("value", "")
        position = None
        if rank_text.split(" of ")[0].strip().isdigit():
            position = int(rank_text.split(" of ")[0].strip())
        audio = fields.get("WordAudio", {}).get("value", "")
        rank = None
        if "_w_r" in audio:
            digits = audio.split("_w_r")[-1].split(".")[0]
            rank = int(digits) if digits.isdigit() else None
        notes.append({"n": position, "rank": rank, "note_id": n.get("noteId"),
                      "term": fields.get("Devanagari", {}).get("value", "")})
    notes.sort(key=lambda m: (m["n"] is None, m["n"]))
    ids = identity()
    manifest = {"deck": ids["deck"], "model": ids["model"], "notes": notes}
    _atomic_json(MANIFEST, manifest)
    unranked = sum(1 for m in notes if m["rank"] is None)
    print(f"reconciled {len(notes)} live note(s) into the manifest"
          + (f" ({unranked} without a recoverable rank)" if unranked else ""))
    return manifest


def check_identity(manifest):
    """Warn when the deck or note type was renamed since the last run.

    Everything is addressed by NAME, so a rename in Anki would otherwise make
    createDeck silently build a second, empty deck and split the collection.
    """
    ids = identity()
    for key, label in (("deck", DECK), ("model", MODEL)):
        was, now = manifest.get(key), ids.get(key)
        if was and now and was != now:
            print(f"WARNING: {key} {label!r} has a different id than last run "
                  f"({was} -> {now}). It was probably renamed or recreated; run "
                  f"--reconcile before adding, or you will split the deck.",
                  file=sys.stderr)
            return False
        if was and now is None:
            print(f"WARNING: {key} {label!r} no longer exists under that name "
                  f"(was id {was}). Rename it back, or update config.py.",
                  file=sys.stderr)
            return False
    return ids


def main():
    dry = "--dry-run" in sys.argv
    if "--reconcile" in sys.argv:
        reconcile()
        return 0

    manifest = load_manifest()
    ids = check_identity(manifest)
    if ids is False and not dry:
        return 2

    items = load_items()
    done = {m["rank"] for m in manifest["notes"] if m.get("rank") is not None}
    start_n = len(manifest["notes"])
    todo = [it for it in items if record.rank(it) not in done][: max(0, TOTAL - start_n)]
    if not todo:
        print("nothing new to add")
        return 0

    # Anki's new-card order follows insertion order, so a late-arriving batch of
    # lower ranks would study out of order forever. v1 did this silently.
    if manifest["notes"]:
        highest = max((m["rank"] for m in manifest["notes"]
                       if m.get("rank") is not None), default=None)
        lowest_new = min(record.rank(it) for it in todo)
        if highest is not None and lowest_new < highest:
            print(f"REFUSING: rank {lowest_new} is lower than rank {highest} "
                  f"already in the deck. Adding now puts a more useful word "
                  f"after a less useful one, permanently.\n"
                  f"  Wait for the missing batches, or pass --allow-unordered "
                  f"if you accept the order.", file=sys.stderr)
            if "--allow-unordered" not in sys.argv:
                return 3

    print(f"adding {len(todo)} notes (positions {start_n+1}..{start_n+len(todo)})",
          file=sys.stderr)
    if dry:
        for it in todo[:10]:
            print(f"  rank {record.rank(it)}  {record.get(it, 'headword')}  "
                  f"{record.get(it, 'gloss')}")
        print(f"  ... {len(todo)} total (dry run, nothing sent)")
        return 0

    added = failed = 0
    for off in range(0, len(todo), 100):
        chunk = todo[off:off + 100]
        notes = [to_note(it, start_n + off + j + 1) for j, it in enumerate(chunk)]
        try:
            res = invoke("addNotes", notes=notes)
        except AnkiError as e:
            # This version of AnkiConnect rolls the whole call back, so nothing
            # from this chunk landed. Everything BEFORE it is already flushed.
            print(f"\nchunk starting at position {start_n+off+1} failed: {e}\n"
                  f"  {added} note(s) already added are recorded in the manifest; "
                  f"re-run to continue from there.", file=sys.stderr)
            _atomic_json(MANIFEST, manifest)
            return 1
        for j, note_id in enumerate(res):
            if note_id is None:
                failed += 1
                continue          # never record a failure as delivered
            added += 1
            it = chunk[j]
            manifest["notes"].append({
                "n": start_n + off + j + 1,
                "rank": record.rank(it),
                "term": record.get(it, "term"),
                "sent_native": record.get(it, "sent_native"),
                "note_id": note_id,
            })
        manifest["deck"], manifest["model"] = ids["deck"], ids["model"]
        _atomic_json(MANIFEST, manifest)     # flush after EVERY chunk
        print(f"  {min(off+100, len(todo))}/{len(todo)}", file=sys.stderr)

    print(f"added {added}, failed {failed}, deck total {len(manifest['notes'])}")
    if failed:
        print("re-run to retry the failures (they were not recorded as done)",
              file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AnkiError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
