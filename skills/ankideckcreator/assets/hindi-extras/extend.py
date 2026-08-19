#!/usr/bin/env python3
"""Add more frequency-ranked candidates on top of an existing prep.py run.

    python extend.py [--count 368] [--batch 184]

Resumes from `batches/candidates.json`, the list prep.py actually used. v1
resumed by re-deriving the frequency list from wordfreq and slicing it by
POSITION, which silently assumes that list is byte-identical to the one the
original build saw. Across the "extend it weeks later" flow the skill promises,
a wordfreq release in between would re-add words already in the deck or skip
some forever.

Importing prep is now safe: its pipeline lives behind a main guard. In v1 the
import alone re-ran everything and rewrote every existing batch file.
"""
import json
import os
import re
import sys

import prep

# Working directory, not this file's location — see prep.py.
BASE = os.getcwd()
BATCHES = os.path.join(BASE, "batches")
CANDIDATES = os.path.join(BATCHES, "candidates.json")


def next_batch_index():
    """One past the HIGHEST existing batch number.

    v1 counted the files instead, so any gap in the sequence (a deleted
    batch_05) made the next write collide with the real top batch and silently
    overwrite it.
    """
    highest = -1
    for name in os.listdir(BATCHES):
        m = re.fullmatch(r"batch_(\d+)\.json", name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def main():
    args = sys.argv[1:]
    count = int(args[args.index("--count") + 1]) if "--count" in args else 368
    batch = int(args[args.index("--batch") + 1]) if "--batch" in args else 184

    if not os.path.isdir(BATCHES):
        print(f"no batches/ directory at {BATCHES} — run prep.py first",
              file=sys.stderr)
        return 2
    if not os.path.exists(CANDIDATES):
        print(f"no {CANDIDATES}.\n"
              "  It is written by prep.py and records the exact candidate list "
              "this build used.\n"
              "  Without it, extending would have to re-derive the list from "
              "wordfreq and hope it has not shifted — which is how ranks drift "
              "out of sync with the deck. Re-run prep.py, or write the file by "
              "hand as {\"count\": N, \"words\": [...]}.", file=sys.stderr)
        return 2

    saved = json.load(open(CANDIDATES, encoding="utf-8"))
    words = saved["words"]
    start = len(words)

    fresh = prep.candidates(start + count)
    if fresh[:start] != words:
        # Same warning either way, but say WHICH assumption broke.
        print("WARNING: the current wordfreq list no longer matches the one this "
              "build started from. Continuing from the SAVED list, so existing "
              "ranks stay valid; the new candidates come from the current list.",
              file=sys.stderr)
    chunk = [w for w in fresh[start:] if w not in set(words)][:count]
    if not chunk:
        print("no new candidates available", file=sys.stderr)
        return 1
    print(f"candidates {start+1}..{start+len(chunk)}", file=sys.stderr)

    pairs = prep.load_tatoeba()
    index = prep.sentence_index(pairs, set(words + chunk))

    items = []
    for i, w in enumerate(chunk):
        rank = start + i + 1
        sent_native = sent_en = source_id = ""
        if index.get(w):
            source_id = index[w][0][1]
            sent_native, sent_en = pairs[source_id]
        items.append({
            "rank": rank,
            "term": w,
            "headword": prep.romanize_word(w),
            "keep": True,
            "gloss": "",
            "pos": "",
            "sent_native": sent_native,
            "sent_display": prep.romanize_sentence(sent_native) if sent_native else "",
            "sent_en": sent_en,
            "source_id": source_id,
        })

    n0 = next_batch_index()
    for j in range(0, len(items), batch):
        path = os.path.join(BATCHES, f"batch_{n0 + j // batch:02d}.json")
        if os.path.exists(path):
            print(f"REFUSING to overwrite {os.path.basename(path)}", file=sys.stderr)
            return 3
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items[j:j + batch], f, ensure_ascii=False, indent=0)
        print("wrote", os.path.basename(path), file=sys.stderr)

    saved["words"] = words + chunk
    saved["count"] = len(saved["words"])
    with open(CANDIDATES, "w", encoding="utf-8") as f:
        json.dump(saved, f, ensure_ascii=False)
    print(f"{sum(1 for i in items if i['sent_native'])}/{len(items)} have a "
          f"tatoeba sentence; candidates.json now holds {saved['count']} words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
