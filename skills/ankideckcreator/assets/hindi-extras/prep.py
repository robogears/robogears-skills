#!/usr/bin/env python3
"""Prep for a Hindi deck: frequency list + Tatoeba sentences + draft Hinglish.

Writes batches/batch_NN.json in the canonical record schema (see
scripts/record.py). Everything here is a DRAFT for builder agents to correct.

    python prep.py [--count 2600] [--batch 184]

Nothing runs on import. v1 had no `if __name__ == "__main__"` guard, so
`import prep` — which extend.py does, purely to reuse two functions — re-ran
this entire pipeline and rewrote every batch file as a side effect.
"""
import json
import os
import re
import sys
from collections import defaultdict

# Resolved against the WORKING directory, not this file's location: you run
# this from the scratchpad deck/ copy, and output must land there. Anchoring
# to __file__ would write batch files into the skill itself.
BASE = os.getcwd()
TATO = os.path.join(BASE, "tatoeba")
# Devanagari LETTERS. The block also holds the danda U+0964, double danda
# U+0965 and the digits U+0966-096F; v1 counted those as letters, so every
# sentence-final word arrived fused to its full stop — missing the exceptions
# table, breaking the word-final vowel rules (पानी। -> "paanee.") and hiding
# sentence-final headwords from the example-sentence index entirely.
DEV_RE = re.compile(r"^[ऀ-ॣ॰-ॿ]+$")
DEV_TOKEN = re.compile(r"[ऀ-ॣ॰-ॿ]+")
DANDA = "।॥"

# ---------- precise-Hinglish conversion ----------
EXCEPTIONS = {
    "है": "hai", "हैं": "hain", "हूँ": "hoon", "हूं": "hoon", "हो": "ho",
    "मैं": "main", "में": "mein", "नहीं": "nahin", "क्या": "kyaa", "क्यों": "kyon",
    "यह": "yeh", "वह": "voh", "ये": "ye", "वो": "vo", "और": "aur",
    "कुछ": "kuchh", "छह": "chhah", "बहुत": "bahut", "कहाँ": "kahaan", "यहाँ": "yahaan",
    "वहाँ": "vahaan", "हाँ": "haan", "अच्छा": "achchhaa", "थोड़ा": "thoRaa",
    "चाहिए": "chaahie", "कोई": "koii", "भाई": "bhaii", "गई": "gaii", "नई": "naii",
    "हुआ": "huaa", "हुई": "huii", "हुए": "hue", "पानी": "paani",
    # ज्ञ is pronounced "gy", never "jn" — see the jñ rule below. These are the
    # common ones; the rule handles the rest.
    "ज्ञान": "gyaan", "विज्ञान": "vigyaan", "ज्ञात": "gyaat",
}

CONS = "kgcjṭḍtdnpbmyrlvśṣshṅñṇzfqxṛ"
VOWELS = "aāiīuūeēoō"


_MEDIAL = re.compile(r"([" + VOWELS + r"])([" + CONS + r"]h?)a([" + CONS + r"]h?[" + VOWELS + r"])")


def _delete_medial_schwa(w: str) -> str:
    """Drop medial schwas the way Hindi does: from the RIGHT, repeatedly.

    Two v1 bugs in one. Its pattern allowed a single consonant CHARACTER on each
    side, so an aspirate digraph blocked the deletion; and substituting
    left-to-right removed the wrong schwa when a word offered two candidates —
    समझदारी came out "samjhadaari" (first schwa gone) instead of "samajhdaari".
    """
    for _ in range(4):
        hit = None
        for i in range(len(w) - 1, -1, -1):
            m = _MEDIAL.match(w, i)
            if m:
                hit = m
                break
        if not hit:
            break
        w = w[:hit.start()] + hit.group(1) + hit.group(2) + hit.group(3) + w[hit.end():]
    return w


def iso_to_hinglish(iso: str) -> str:
    w = iso
    # nuqta digraphs from ISO
    w = w.replace("k͟h", "kh").replace("ġ", "g")
    # ज्ञ = "gy" in speech, not the letter-by-letter "jn". The spec demands
    # SPOKEN forms; v1 had no rule at all, so ज्ञान drafted as "jnaan" while the
    # audio (spoken from the Devanagari) said "gyaan".
    w = w.replace("jñ", "gy")
    # nasals: candrabindu/anusvara. The alternation is spelled out rather than
    # put in a character class — "m̐" is TWO codepoints (m + U+0310), so v1's
    # [ṁm̐] silently matched a bare combining mark and doubled the m
    # (काँपना -> "kaammpnaa").
    w = re.sub(r"(?:ṁ|m̐)([pbm])", r"m\1", w)
    w = w.replace("m̐", "n").replace("ṁ", "n")
    # schwa deletion: final, then medial passes (VC a CV)
    if len(w) > 2:
        w = re.sub(r"([" + CONS + r"h])a$", r"\1", w)
    w = _delete_medial_schwa(w)
    # retroflex + aspirates (longest first)
    for a, b in [("ṭh", "Th"), ("ḍh", "Dh"), ("ṛh", "Rh"), ("ṭ", "T"), ("ḍ", "D"),
                 ("ṛ", "R"), ("r̥", "ri")]:
        w = w.replace(a, b)
    # ca-row. Done with a placeholder so the rules cannot consume each other's
    # output: v1 ran cch->chchh and then ch->chh, and the second rule re-matched
    # inside the first one's result, so अच्छे drafted as "achhchhhe".
    w = w.replace("cch", "\x00").replace("ch", "\x01").replace("c", "ch")
    w = w.replace("\x01", "chh").replace("\x00", "chchh")
    # sibilants, nasal variants
    w = w.replace("ś", "sh").replace("ṣ", "sh")
    w = w.replace("ṅ", "n").replace("ñ", "n").replace("ṇ", "n")
    # vowels: word-final ī/ū shorten by convention — but NOT after another
    # vowel, where the deck's conventional spellings keep the doubled form
    # (koii, bhaii, davaaii). v1 shortened those too, drafting "davaai".
    w = re.sub(r"(?<=[" + VOWELS + r"])ī$", "ii", w)
    w = re.sub(r"(?<=[" + VOWELS + r"])ū$", "uu", w)
    w = re.sub(r"(?<![" + VOWELS + r"i])ī$", "i", w)
    w = re.sub(r"(?<![" + VOWELS + r"u])ū$", "u", w)
    for a, b in [("ā", "aa"), ("ī", "ee"), ("ū", "oo"), ("ē", "e"), ("ō", "o")]:
        w = w.replace(a, b)
    return w


def romanize_word(dev: str) -> str:
    from indic_transliteration import sanscript
    dev = dev.strip(DANDA)
    if dev in EXCEPTIONS:
        return EXCEPTIONS[dev]
    iso = sanscript.transliterate(dev, sanscript.DEVANAGARI, sanscript.ISO)
    return iso_to_hinglish(iso)


def romanize_sentence(dev_sent: str) -> str:
    out = []
    for tok in re.split(r"(\s+)", dev_sent):
        cleaned = tok.replace("।", ".").replace("॥", ".")
        parts = DEV_TOKEN.split(cleaned)
        devs = DEV_TOKEN.findall(cleaned)
        buf = parts[0]
        for d, p in zip(devs, parts[1:]):
            buf += romanize_word(d) + p
        out.append(buf)
    return "".join(out)


def load_tatoeba():
    """hin_id -> (hindi text, english text)."""
    hin = {}
    with open(os.path.join(TATO, "hin_sentences.tsv"), encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3:
                hin[p[0]] = p[2]
    eng_needed, links = {}, defaultdict(list)
    with open(os.path.join(TATO, "hin-eng_links.tsv"), encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 2 and p[0] in hin:
                links[p[0]].append(p[1])
                eng_needed[p[1]] = None
    with open(os.path.join(TATO, "eng_sentences.tsv"), encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] in eng_needed:
                eng_needed[p[0]] = p[2]
    pairs = {}
    for hid, eids in links.items():
        for eid in eids:
            if eng_needed.get(eid):
                pairs[hid] = (hin[hid], eng_needed[eid])
                break
    return pairs


def sentence_index(pairs, common):
    """Best (shortest, most-familiar) sentence per word."""
    index = defaultdict(list)
    for hid, (ht, _) in pairs.items():
        toks = [t.strip(DANDA) for t in DEV_TOKEN.findall(ht)]
        toks = [t for t in toks if t]
        if not (3 <= len(toks) <= 14):
            continue
        unknown = sum(1 for t in toks if t not in common)
        score = len(toks) + unknown * 4
        for t in set(toks):
            index[t].append((score, hid))
    for t in index:
        index[t].sort()
    return index


def candidates(count):
    from wordfreq import top_n_list
    raw = top_n_list("hi", count * 2)
    words = []
    for w in raw:
        if not DEV_RE.match(w):
            continue
        if len(w) == 1 and w in "ऀँंःऺऻ़ािीुूृॄॅॆेैॉॊोौ्":
            continue
        words.append(w)
    return words[:count]


def main():
    args = sys.argv[1:]
    # SKILL.md's own rule is ~30% overshoot because roughly a quarter of
    # candidates get rejected. v1 asked for 2200 to fill a 2000-card deck — 10%
    # — which is exactly why extend.py had to exist to top the deck up.
    count = int(args[args.index("--count") + 1]) if "--count" in args else 2600
    batch = int(args[args.index("--batch") + 1]) if "--batch" in args else 184

    words = candidates(count)
    print(f"candidates: {len(words)}", file=sys.stderr)

    pairs = load_tatoeba()
    print(f"tatoeba hi-en pairs: {len(pairs)}", file=sys.stderr)
    index = sentence_index(pairs, set(words))

    items = []
    for rank, w in enumerate(words, 1):
        sent_native = sent_en = source_id = ""
        if index.get(w):
            source_id = index[w][0][1]
            sent_native, sent_en = pairs[source_id]
        items.append({
            "rank": rank,
            "term": w,
            "headword": romanize_word(w),
            "keep": True,
            "gloss": "",
            "pos": "",
            "sent_native": sent_native,
            "sent_display": romanize_sentence(sent_native) if sent_native else "",
            "sent_en": sent_en,
            "source_id": source_id,
        })

    with_s = sum(1 for i in items if i["sent_native"])
    print(f"items with a tatoeba sentence: {with_s}/{len(items)}", file=sys.stderr)

    out_dir = os.path.join(BASE, "batches")
    os.makedirs(out_dir, exist_ok=True)
    # Save the candidate list so a later extend run resumes from what THIS build
    # actually saw, instead of re-deriving it from a wordfreq release that may
    # have shifted in the meantime.
    with open(os.path.join(out_dir, "candidates.json"), "w", encoding="utf-8") as f:
        json.dump({"count": count, "words": words}, f, ensure_ascii=False)
    n = 0
    for i in range(0, len(items), batch):
        path = os.path.join(out_dir, f"batch_{n:02d}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items[i:i + batch], f, ensure_ascii=False, indent=0)
        n += 1
    print(f"wrote {n} batch files + candidates.json", file=sys.stderr)


if __name__ == "__main__":
    main()
