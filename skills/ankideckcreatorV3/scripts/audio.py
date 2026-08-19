#!/usr/bin/env python3
"""Generate neural TTS audio into Anki's media folder.

Clips are named by rank, so they stay valid no matter how deck positions are
renumbered. Safe to stop and restart at any point.

The v1 version could DESTROY audio it had already made. edge-tts opens the
destination file for writing before the network stream is consumed, so a failed
regeneration truncated a good clip, and the error handler then deleted it —
leaving a live card pointing at a file that no longer existed. Worse, the
freshness stamps were saved only at the very end, so an interrupted run lost
them all and the next run re-queued every unchanged clip for regeneration. Here
every clip is synthesized to a temp file and moved into place only after it is
known good, and stamps are flushed as the run proceeds.

Card text is sent to a Microsoft endpoint by edge-tts (an unofficial client) to
be spoken. Fine for a frequency list; think before running it on a deck built
from private material.

Usage:
  audio.py                 generate everything missing or stale
  audio.py --limit 20      only the first 20 ranks (a smoke test)
  audio.py --dry-run       report what would be generated, touch nothing
"""
import asyncio
import glob
import hashlib
import json
import os
import sys

import edge_tts

import record
from config import BASE, CONCURRENCY, STAMPS, TOTAL, VOICE, sentence_clip, word_clip

PROGRESS_EVERY = 50


def _atomic_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def content():
    """Exactly the items add_notes.py will deliver, keyed by rank.

    Imported from add_notes so the two cannot diverge: v1 generated clips for
    every kept record, including spelling variants the loader collapses away and
    ranks beyond the deck cap, littering the live media folder with orphans.
    """
    from add_notes import load_items
    out = {}
    for it in load_items(verbose=False)[:TOTAL]:
        rank = record.rank(it)
        if rank is not None:
            out[rank] = it
    return out


def _hash(text):
    """Fingerprint the text AND the voice.

    v1 hashed only the text, so switching VOICE left every existing clip
    reporting "skip" — the deck kept the old voice forever with no way to notice.
    """
    return hashlib.sha1(f"{VOICE}\x00{text}".encode("utf-8")).hexdigest()[:16]


async def synth(sem, text, path, stamps, tries=3):
    """Generate one clip. Never destroys an existing good file."""
    if not text:
        return "skip"
    key = os.path.basename(path)
    if os.path.exists(path) and stamps.get(key) == _hash(text):
        return "skip"

    tmp = path + f".part{os.getpid()}"
    err = ""
    for attempt in range(tries):
        async with sem:
            try:
                await edge_tts.Communicate(text, VOICE).save(tmp)
                if os.path.getsize(tmp) > 500:
                    os.replace(tmp, path)     # atomic; the old clip lives until now
                    stamps[key] = _hash(text)
                    return "ok"
                err = "empty file"
            except Exception as e:                      # noqa: BLE001
                err = str(e)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)            # only ever the temp file
        # Backoff OUTSIDE the semaphore: v1 slept while holding a slot, so one
        # failing clip throttled every healthy one during an outage.
        if attempt < tries - 1:
            await asyncio.sleep(1.5 * (attempt + 1))
    print(f"FAIL {os.path.basename(path)}: {err[:90]}", file=sys.stderr)
    return "fail"


async def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    limit = TOTAL
    if "--limit" in args:
        i = args.index("--limit") + 1
        if i >= len(args) or not args[i].isdigit():
            print("--limit needs a number, e.g. --limit 20", file=sys.stderr)
            return 2
        limit = int(args[i])

    try:
        from anki import media_dir
        media = media_dir()
    except Exception as e:                              # noqa: BLE001
        print(f"cannot ask Anki for its media folder ({e}).\n"
              "  Open Anki (AnkiConnect must be installed), or set MEDIA in "
              "config.py.", file=sys.stderr)
        return 2
    if not os.path.isdir(media):
        print(f"media folder does not exist: {media}", file=sys.stderr)
        return 2
    print(f"media folder: {media}", file=sys.stderr)

    items = content()
    stamps = json.load(open(STAMPS, encoding="utf-8")) if os.path.exists(STAMPS) else {}

    jobs = []
    for rank in sorted(items)[:limit]:
        it = items[rank]
        jobs.append((record.get(it, "term"),
                     os.path.join(media, word_clip(rank))))
        if record.has_sentence(it):
            jobs.append((record.get(it, "sent_native"),
                         os.path.join(media, sentence_clip(rank))))

    pending = [(t, p) for t, p in jobs
               if t and not (os.path.exists(p) and stamps.get(os.path.basename(p)) == _hash(t))]
    print(f"{len(jobs)} clips for {len(items)} cards; {len(pending)} need generating",
          file=sys.stderr)
    if dry:
        for _, p in pending[:10]:
            print(f"  would generate {os.path.basename(p)}")
        print(f"  ... {len(pending)} total (dry run, nothing written)")
        return 0
    if not pending:
        print("all clips already current")
        return 0

    sem = asyncio.Semaphore(CONCURRENCY)
    done = {"n": 0}
    results = []

    async def run(text, path):
        res = await synth(sem, text, path, stamps)
        results.append(res)
        done["n"] += 1
        # v1 printed nothing between "queued" and the final tally — for a 2k deck
        # that is ~4000 network calls of total silence, indistinguishable from a
        # hang, and killing it used to be expensive.
        if done["n"] % PROGRESS_EVERY == 0 or done["n"] == len(pending):
            _atomic_json(STAMPS, stamps)      # flush as we go, not just at the end
            print(f"  {done['n']}/{len(pending)}  "
                  f"ok={results.count('ok')} fail={results.count('fail')}",
                  file=sys.stderr)

    try:
        await asyncio.gather(*(run(t, p) for t, p in pending))
    finally:
        _atomic_json(STAMPS, stamps)          # survives Ctrl-C too

    failed = results.count("fail")
    print(f"generated {results.count('ok')}, skipped {results.count('skip')}, "
          f"failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
