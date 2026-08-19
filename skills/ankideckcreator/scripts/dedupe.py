#!/usr/bin/env python3
"""Classify same-headword cards as spelling variants or true homographs.

Hindi tolerates several spellings of one word (मंदिर/मन्दिर, हूँ/हूं, बिल्कुल/
बिलकुल, दुख/दुःख), which yield two identical-looking cards; those collapse to
one. But की/कि and जाती/जाति are different words that merely romanize alike, and
है/हैं differ only by a nasal mark yet mean "is" and "are"; those must all stay.

So orthography alone cannot decide it. The test is a shared consonant skeleton
AND overlapping meaning.

Not a command-line tool — SKILL.md v1 said "run all of these" and listed it,
which made a literal `python dedupe.py` a silent no-op. It is a library applied
by add_notes.py at delivery time. `python dedupe.py --selftest` runs the
classifier against the known pairs in references/hindi.md.
"""
import re
import sys
import unicodedata

import record

NUKTA, ANUSVARA, CANDRABINDU, VISARGA, VIRAMA = "़", "ं", "ँ", "ः", "्"
MATRAS = "ािीुूृॄॅॆेैॉॊोौ"
# Stops only (ka-varga..pa-varga). The nasal+virama == anusvara equivalence
# holds for homorganic stops; य र ल व श ष स ह form genuine conjuncts instead.
# Written as codepoints: the क़ family is base + nuqta (two codepoints), so a
# literal range endpoint would silently be the nuqta itself.
STOP_CLASS = "[\u0915-\u092e]"

# Words that carry no distinguishing meaning. "are"/"was"/"were" join the list
# because a gloss of "are" would otherwise be a real content word: v1's docstring
# claimed है/हैं were kept apart by the stopword test when in fact they scraped
# past on an empty-set edge case, and a gloss pair like "is/are" vs "are" merged
# the two — collapsing "is" and "are" into one card.
STOPWORDS = r"\b(to|a|an|the|of|is|be|are|was|were|am|been)\b"


def orthographic_key(dev: str, drop_nukta: bool = True) -> str:
    """Collapse spellings that are unambiguously the same sound.

    `drop_nukta=False` keeps the dot, which is how same_word tells a true
    spelling variant (खुद/ख़ुद — same word, optional dot) from two different
    words that only look alike once the dot is gone (फन "snake hood" vs फ़न
    "art, skill" — genuinely different consonants).
    """
    s = unicodedata.normalize("NFC", dev)
    if drop_nukta:
        s = s.replace(NUKTA, "")
    s = s.replace(CANDRABINDU, ANUSVARA).replace(VISARGA, "")
    # nasal + virama before a STOP == anusvara (मन्दिर == मंदिर). Restricted to
    # the stop series: न्य in अन्य and म्ह in तुम्हें are genuine conjuncts, not
    # anusvara spellings. v1 omitted the lookahead its own comment described and
    # mangled both.
    s = re.sub(r"[ङञणनम]" + VIRAMA + f"(?={STOP_CLASS})", ANUSVARA, s)
    # epenthetic य in the -िये/-िए ending (चाहिये == चाहिए, गये == गए). Requires
    # a preceding consonant or matra, so the standalone pronoun ये survives and
    # a conjunct like प्रत्येक (virama before ये) is left alone. v1's bare
    # replace collapsed the pronoun ये to the vowel ए.
    s = re.sub(r"(?<=[" + MATRAS + r"अ-औक-ह])ये", "ए", s)
    return s


def skeleton(dev: str) -> str:
    """Consonants and independent vowels — every matra and diacritic stripped.

    (Independent vowel letters survive by design: आम keeps its आ, which is what
    keeps genuinely different words apart. v1's docstring claimed every vowel
    was stripped, which was never true.)
    """
    s = unicodedata.normalize("NFC", dev).replace(NUKTA, "")
    return "".join(c for c in s
                   if c not in MATRAS + ANUSVARA + CANDRABINDU + VISARGA + VIRAMA)


def _gloss_words(gloss: str) -> set:
    g = str(gloss or "").lower()
    g = re.sub(r"\(.*?\)", " ", g)
    g = re.sub(STOPWORDS, " ", g)
    g = re.sub(r"[^a-z ]", " ", g)
    return {w for w in g.split() if len(w) > 1}


def gloss_overlap(a: str, b: str) -> float:
    wa, wb = _gloss_words(a), _gloss_words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _lead_word(gloss: str) -> str:
    """The FIRST meaningful word of the first clause.

    v1 sorted the clause alphabetically and took [0], so "come back, return"
    reported 'back' as its lead — an arbitrary token that could merge two
    different words by coincidence.
    """
    first = re.split(r"[;,]", str(gloss or ""))[0]
    g = re.sub(r"\(.*?\)", " ", first.lower())
    g = re.sub(STOPWORDS, " ", g)
    g = re.sub(r"[^a-z ]", " ", g)
    for w in g.split():
        if len(w) > 1:
            return w
    return ""


def _shared_lead(ga: str, gb: str) -> bool:
    """Synonym-y glosses that still lead with the same word."""
    fa, fb = _lead_word(ga), _lead_word(gb)
    return bool(fa) and fa == fb


def same_word(a, b) -> bool:
    """Same word spelled two ways, rather than two words spelled alike."""
    ta, tb = record.get(a, "term"), record.get(b, "term")
    ga, gb = record.get(a, "gloss"), record.get(b, "gloss")

    if orthographic_key(ta, drop_nukta=False) == orthographic_key(tb, drop_nukta=False):
        # Identical once nasal/visarga/epenthetic spellings are normalised, with
        # the nuqta still in place: one word, two spellings (हूँ/हूं). Safe.
        return True
    if orthographic_key(ta) == orthographic_key(tb):
        # Only equal AFTER stripping the nuqta. Usually a variant (खुद/ख़ुद) but
        # sometimes two different words (फन "snake hood" vs फ़न "art, skill"),
        # which v1 merged with no meaning check at all — one card silently
        # dropped. Require agreeing meaning.
        return gloss_overlap(ga, gb) >= 0.3 or _shared_lead(ga, gb)
    # identical consonants plus overlapping meaning: यानी/यानि, बिल्कुल/बिलकुल.
    # है/हैं share a skeleton but mean is/are; their glosses are stopwords and
    # score zero, so the meaning test keeps them apart.
    if skeleton(ta) == skeleton(tb):
        return gloss_overlap(ga, gb) >= 0.3 or _shared_lead(ga, gb)
    return False


def hint_for(item) -> str:
    """Front-of-card disambiguator for a genuine homograph.

    Part of speech only — it separates की from कि without revealing the English
    meaning, so the card still tests recall. An explicit `hint` on the record
    wins, which is how a same-part-of-speech pair gets separated: माँ and मान
    are BOTH nouns, so v1 gave them identical fronts and made the pair
    unguessable. The content spec asks for gender on nouns ("noun f." vs
    "noun m."), which resolves that pair without leaking meaning; lint.py flags
    any homograph group whose hints are still identical.
    """
    explicit = str(record.get(item, "hint", "")).strip()
    if explicit:
        return explicit
    return str(record.get(item, "pos", "")).strip()


def hints_distinct(group) -> bool:
    """Do the members of a homograph group get distinguishable fronts?"""
    hints = [hint_for(i).lower() for i in group]
    return all(hints) and len(set(hints)) == len(hints)


def _selftest():
    """Check the classifier on the pairs references/hindi.md calls out."""
    def rec(term, gloss, pos=""):
        return {"term": term, "gloss": gloss, "pos": pos}

    keep_apart = [
        (rec("की", "of"), rec("कि", "that")),
        (rec("जाती", "goes (f.)"), rec("जाति", "caste")),
        (rec("माँ", "mother"), rec("मान", "respect")),
        (rec("है", "is"), rec("हैं", "are")),
        (rec("फन", "snake hood"), rec("फ़न", "art, skill")),
        (rec("दिन", "day"), rec("दान", "donation")),
    ]
    collapse = [
        (rec("मंदिर", "temple"), rec("मन्दिर", "temple")),
        (rec("हूँ", "am, I am"), rec("हूं", "am, I am")),
        (rec("खुद", "self"), rec("ख़ुद", "self")),
        (rec("चाहिये", "should, need"), rec("चाहिए", "should, need")),
        (rec("बिल्कुल", "absolutely, totally"), rec("बिलकुल", "absolutely, completely")),
        (rec("दुख", "sorrow"), rec("दुःख", "sorrow")),
    ]
    keys = [("ये", "ये"), ("अन्य", "अन्य")]
    fails = 0
    for a, b in keep_apart:
        if same_word(a, b):
            print(f"FAIL merged: {a['term']} + {b['term']}")
            fails += 1
    for a, b in collapse:
        if not same_word(a, b):
            print(f"FAIL kept apart: {a['term']} + {b['term']}")
            fails += 1
    for src, want in keys:
        if orthographic_key(src) != want:
            print(f"FAIL key {src} -> {orthographic_key(src)} (want {want})")
            fails += 1
    if orthographic_key("गये") != orthographic_key("गए"):
        print("FAIL गये != गए")
        fails += 1
    total = len(keep_apart) + len(collapse) + len(keys) + 1
    print(f"dedupe selftest: {total - fails}/{total} passed")
    return fails


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(1 if _selftest() else 0)
    print("dedupe.py is a library, not a step: add_notes.py applies it at "
          "delivery time.\nRun `python dedupe.py --selftest` to check the "
          "classifier on known pairs.", file=sys.stderr)
    sys.exit(2)
