#!/usr/bin/env python3
"""Deterministic spec-compliance linter for card content.

Agents catch semantic errors; this catches mechanical ones, more reliably and
for free.

Usage:
  lint.py <dir> [<dir> ...] [--fix]
  lint.py --merged            lint built/ overlaid by final/ — the set that
                              actually SHIPS, which v1 could never see
  lint.py final --fix         repair the mechanical issues in place

What changed from v1, beyond new rules:
  * --fix no longer capitalizes sentence openings. In this romanization a
    capital MEANS retroflex, so that "repair" forged pronunciation marks and
    fought the linter's own stray_capital rule.
  * a record missing `rank` is reported, not a crash that discards the report.
  * a bad directory argument is an error, not a silent "0 files, all clean".
  * --fix only rewrites files it actually changed, and writes them atomically.
"""
import glob
import json
import os
import re
import sys
from collections import defaultdict

import record
from config import ACCENT_CODE, BASE
from dedupe import hint_for, hints_distinct, same_word
from markup import (DEV_RE, count_retroflex_dev, count_retroflex_marks,
                    native_words, normalize_headword, target_in_sentence)

ACCENT = ACCENT_CODE == "hindi-retroflex"
WORD = re.compile(r"[A-Za-z]+")
HTMLISH = re.compile(r"[<>]|&[a-z]{2,8};|&#\d+;")

# Word-final doubled i/u is wrong per spec, except these conventional spellings.
FINAL_OK = {"koii", "gaii", "naii", "bhaii", "huii", "laii", "aaii", "kaii",
            "daii", "maii"}

# The spec's conventional spellings for ultra-common words (references/hindi.md).
# v1 enforced exactly two spelling rules, so "kya", "nahi" and "acchaa" all
# passed and a dozen independent builder agents could ship the same word spelled
# differently on different cards.
CANONICAL = {
    "yah": "yeh", "vah": "voh", "hei": "hai", "hain'": "hain",
    "kya": "kyaa", "kyun": "kyon", "kyu": "kyon",
    "nahi": "nahin", "nahiin": "nahin", "nahee": "nahin",
    "acchaa": "achchhaa", "achha": "achchhaa", "acha": "achchhaa",
    "hun": "hoon", "hoo": "hoon", "chahiye": "chaahie", "chaahiye": "chaahie",
    "kuch": "kuchh", "bohot": "bahut", "bahot": "bahut",
    "tha": "thaa", "hua": "huaa",
    "kaha": "kahaan", "yaha": "yahaan", "vaha": "vahaan",
}


def _atomic_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=0)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def norm_words(s):
    return WORD.findall(str(s or ""))



def _native_present(native, sent):
    """Is the native headword demonstrated by the sentence?

    A contiguous substring test is right for a single word but wrong for a
    grammar PATTERN, which is normally discontinuous: "मुझे चाहिए" shows up as
    "मुझे एक कप चाय चाहिए". Require every part, in order, rather than adjacent —
    otherwise every pattern card in a grammar deck is flagged and the real
    misses drown in the noise.
    """
    if native in sent:
        return True
    parts = [p for p in native.split() if p]
    if len(parts) < 2:
        return False
    pos = 0
    for part in parts:
        found = sent.find(part, pos)
        if found < 0:
            return False
        pos = found + len(part)
    return True

def lint_record(it, seen_ranks, issues):
    r = record.rank(it)
    tag = f"rank {r if r is not None else '?'} ({record.get(it, 'headword') or '?'})"
    if r is None:
        # v1 put None into the rank set and later crashed on max(), discarding
        # every finding it had collected.
        issues["missing_rank"].append(tag)
    elif r in seen_ranks:
        issues["dup_rank"].append(tag)
    else:
        seen_ranks.add(r)

    for field in record.missing_fields(it):
        issues["empty_field"].append(f"{tag}: {field}")
    if not str(record.get(it, "pos")).strip():
        issues["empty_field"].append(f"{tag}: pos")

    head = str(record.get(it, "headword"))
    native = str(record.get(it, "term"))
    sd = str(record.get(it, "sent_native"))
    sh = str(record.get(it, "sent_display"))

    for field in ("headword", "sent_display", "gloss", "sent_en"):
        v = str(record.get(it, field))
        if ACCENT and DEV_RE.search(v):
            issues["native_leak"].append(f"{tag}: {field}={v[:40]}")
        # Content must not carry markup: it is escaped on the way into Anki, so
        # angle brackets here mean the source text itself is wrong (or hostile —
        # Tatoeba is a community corpus).
        if HTMLISH.search(v):
            issues["raw_html"].append(f"{tag}: {field}={v[:40]}")
        # "No diacritics, ever" — catches ISO leftovers (ā, ī, ṭ) that survived
        # a draft conversion. v1 had this check; do not drop it.
        stray = [c for c in v if ord(c) > 127 and c not in "'’-–—.,!?()"]
        if ACCENT and stray:
            issues["non_ascii"].append(f"{tag}: {field}={v[:40]}")
    for field in ("term", "sent_native"):
        if HTMLISH.search(str(record.get(it, field))):
            issues["raw_html"].append(f"{tag}: {field}")

    if ACCENT:
        _lint_accent(it, tag, head, native, sh, sd, issues)

    if it.get("keep"):
        if bool(sh) != bool(sd):
            issues["sentence_pair"].append(f"{tag}: display={bool(sh)} native={bool(sd)}")
        if sh and not str(record.get(it, "sent_en")).strip():
            issues["missing_sent_en"].append(tag)
        if sd and native and not _native_present(native, sd):
            issues["word_not_in_sentence"].append(f"{tag}: {native} not in {sd[:30]}")
        if sh and head and not target_in_sentence(sh, head):
            # Advisory: either the sentence really lacks the target, or the
            # inflection is too distant to match — in which case the card ships
            # with nothing highlighted. v1 checked only the native side and
            # never noticed the second case at all.
            issues["target_not_marked"].append(f"{tag}: {head!r} not found in {sh[:40]!r}")
        if sh and sd:
            nh = len(norm_words(sh))
            nd = len(native_words(sd))
            if nd and abs(nh - nd) > max(2, nd * 0.4):
                issues["length_mismatch"].append(f"{tag}: {nh} roman vs {nd} native words")


def _lint_accent(it, tag, head, native, sh, sd, issues):
    """Checks that only make sense for a capital-means-retroflex romanization."""
    for w in norm_words(head) + norm_words(sh):
        lw = w.lower()
        if lw in CANONICAL:
            issues["spelling_form"].append(f"{tag}: '{w}' should be {CANONICAL[lw]}")
        if _bad_final_vowel(lw):
            issues["final_vowel"].append(f"{tag}: word '{w}'")

    # Capitals, BOTH directions. v1 only ever asked whether a capital that was
    # present was justified — never whether a required one was missing, so
    # "larkaa" for लड़का shipped with no marigold at all, which under the deck's
    # one-code design positively teaches "dental".
    if head and native:
        marks, real = count_retroflex_marks(head), count_retroflex_dev(native)
        if marks > real:
            issues["retroflex_over"].append(
                f"{tag}: headword claims {marks} retroflex, {native} has {real}")
        elif marks < real:
            issues["retroflex_under"].append(
                f"{tag}: headword marks {marks} retroflex, {native} has {real} "
                f"— is a capital T/D/R missing?")

    words = native_words(sd)
    roman = norm_words(sh)
    if sh and sd and len(words) == len(roman):
        for rw, nw in zip(roman, words):
            m, real = count_retroflex_marks(rw), count_retroflex_dev(nw)
            if m > real:
                issues["retroflex_over"].append(f"{tag}: sentence '{rw}' vs {nw}")
            elif m < real:
                issues["retroflex_under"].append(f"{tag}: sentence '{rw}' vs {nw}")
    elif sh and sd:
        # The pair could not be aligned, so no per-word check ran here and
        # markup.normalize_caps could not verify capitals either.
        issues["unaligned_sentence"].append(
            f"{tag}: {len(roman)} roman vs {len(words)} native words")

    for w in roman:
        if w[0].isupper() and w[0] not in "TDR":
            issues["stray_capital"].append(f"{tag}: '{w}'")


def lint_groups(items, issues):
    """Cross-record checks — homograph hints and duplicate sentences."""
    heads = defaultdict(list)
    for it in items:
        if not it.get("keep"):
            continue
        head = record.get(it, "headword")
        if ACCENT:
            head = normalize_headword(head, record.get(it, "term"))
        heads[head].append(it)
    for head, group in heads.items():
        if len(group) < 2:
            continue
        distinct = [g for i, g in enumerate(group)
                    if not any(same_word(group[j], g) for j in range(i))]
        if len(distinct) > 1 and not hints_distinct(distinct):
            issues["homograph_hint"].append(
                f"{head!r}: {', '.join(record.get(i, 'term') for i in distinct)} "
                f"all show the hint {hint_for(distinct[0])!r}")

    sentences = defaultdict(list)
    for it in items:
        if it.get("keep") and record.get(it, "sent_native"):
            sentences[record.get(it, "sent_native")].append(record.rank(it))
    for sent, ranks in sentences.items():
        if len(ranks) > 1:
            issues["duplicate_sentence"].append(f"ranks {ranks}: {sent[:40]}")


def _bad_final_vowel(lw):
    """Word-final doubled i/u is a spec violation — unless it follows a vowel.

    The spec's own conventional spellings (koii, bhaii, davaaii, sunaaii) all
    have a vowel before the doubled one, and prep.py now generates them, so a
    fixed allowlist both misses new words and — via --fix — truncated the very
    spellings the spec requires.
    """
    if not (lw.endswith("ii") or lw.endswith("uu")):
        return False
    if lw in FINAL_OK:
        return False
    stem = lw[:-2]
    return not (stem and stem[-1] in "aeiou")


def fix_record(it):
    """Repair only the mechanical, unambiguous problems."""
    if not ACCENT:
        # Every rule below is Hindi romanization policy. On a plain deck it
        # would mangle ordinary English (v1 applied them unconditionally).
        return False
    changed = False

    def fix_text(s):
        nonlocal changed
        out = s
        for wrong, right in CANONICAL.items():
            out = re.sub(rf"\b{wrong}\b", right, out)
            out = re.sub(rf"\b{wrong.capitalize()}\b", right, out)
        def final_vowel(m):
            w = m.group(0)
            return w[:-1] if _bad_final_vowel(w.lower()) else w
        out = re.sub(r"\b[A-Za-z]*(?:ii|uu)\b", final_vowel, out)
        out = out.replace("।", ".")
        if out != s:
            changed = True
        return out

    for canon in ("headword", "sent_display"):
        for key in record.ALIASES[canon]:
            if it.get(key):
                it[key] = fix_text(it[key])
    # NOTE: v1 also uppercased the first letter of every sentence here. That
    # contradicted design.md ("Never 'fix' lowercase sentence openings"),
    # SKILL.md, and this file's own stray_capital rule — and forged a retroflex
    # claim on any sentence starting with t/d/r. Deliberately not reinstated.
    return changed


def collect(paths):
    """Load records from directories, and report a bad path instead of hiding it."""
    files = []
    for d in paths:
        if d.startswith("-"):
            print(f"'{d}' looks like a flag, not a directory — check the argument "
                  f"order (usage: lint.py <dir> [--fix])", file=sys.stderr)
            return None
        if not os.path.isdir(d):
            print(f"not a directory: {d}", file=sys.stderr)
            return None
        found = sorted(glob.glob(os.path.join(d, "*.json")))
        if not found:
            print(f"no .json files in {d}", file=sys.stderr)
            return None
        files.extend(found)
    return files


def merged_files():
    """built/ overlaid by final/, per batch — the exact set add_notes delivers."""
    files = {}
    for tier in ("built", "final"):
        for path in sorted(glob.glob(os.path.join(BASE, tier, "batch_*.json"))):
            files[os.path.basename(path)] = path
    return [files[k] for k in sorted(files)]


def main():
    args = [a for a in sys.argv[1:]]
    do_fix = "--fix" in args
    merged = "--merged" in args
    dirs = [a for a in args if not a.startswith("-")]

    if merged:
        files = merged_files()
        if not files:
            print("no built/ or final/ batch files found", file=sys.stderr)
            return 2
        if do_fix:
            print("--merged is read-only (it spans two tiers); "
                  "run --fix on a single directory", file=sys.stderr)
            return 2
    elif dirs:
        files = collect(dirs)
        if files is None:
            return 2
    else:
        print(__doc__.strip().split("\n\n")[2], file=sys.stderr)
        return 2

    issues = defaultdict(list)
    seen, all_items = set(), []
    total = fixed = 0
    for path in files:
        data = json.load(open(path, encoding="utf-8"))
        dirty = False
        for it in data:
            total += 1
            if do_fix and fix_record(it):
                fixed += 1
                dirty = True
            lint_record(it, seen, issues)
            all_items.append(it)
        # v1 rewrote every file on --fix even when nothing changed, widening the
        # window for a truncating crash over expensive agent output.
        if do_fix and dirty:
            _atomic_json(path, data)
    lint_groups(all_items, issues)

    print(f"files={len(files)} records={total} fixed={fixed}")
    if seen:
        missing = [r for r in range(1, max(seen) + 1) if r not in seen]
        if missing:
            print(f"MISSING RANKS: {len(missing)} e.g. {missing[:10]}")
    for k in sorted(issues, key=lambda k: -len(issues[k])):
        v = issues[k]
        print(f"\n{k}: {len(v)}")
        for x in v[:6]:
            print("   ", x)
    hard = sum(len(v) for k, v in issues.items()
               if k not in ("target_not_marked", "unaligned_sentence",
                            "length_mismatch"))
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
