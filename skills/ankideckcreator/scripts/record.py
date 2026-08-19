"""The canonical card-record schema, in one place.

v1's docs told builder agents to write
    {rank, term, keep, reason?, headword, gloss, pos, sent_native, sent_display,
     sent_en, source_id}
while every script read the original Hindi build's names
    {rank, dev, keep, hinglish, gloss, pos, sent_dev, sent_hinglish, sent_en,
     tatoeba_id}
Nothing bridged them, so a build that followed the documentation crashed at the
audio and delivery phases and got a misleading all-clear from the linter.

The documented names are canonical here. The Hindi aliases are still accepted so
batch files from the original Core 2k build keep working.

Meaning of each field, for any deck:
    rank         int  — usefulness rank, 1 = most useful. Audio filenames key on it.
    term         str  — the item in its source form. THIS IS WHAT TTS SPEAKS.
                        Devanagari for Hindi; the plain word for an English deck.
    headword     str  — what the learner reads on the front (romanization, or
                        the same as `term` when there is no separate script).
    keep         bool — false rejects the candidate; keep it in the file with a
                        `reason` — that is the audit trail.
    gloss        str  — concise meaning, 1-5 words.
    pos          str  — part of speech / category, incl. gender where relevant.
    sent_native  str  — example sentence in the source form. SPOKEN BY TTS.
    sent_display str  — the same sentence as displayed to the learner.
    sent_en      str  — translation / explanation of the sentence.
    source_id    str  — provenance, e.g. a Tatoeba id. Empty = written for the deck.
    hint         str  — optional explicit front-of-card disambiguator; normally
                        left empty and derived from `pos`.
"""

ALIASES = {
    "term": ("term", "dev"),
    "headword": ("headword", "hinglish"),
    "sent_native": ("sent_native", "sent_dev"),
    "sent_display": ("sent_display", "sent_hinglish"),
    "source_id": ("source_id", "tatoeba_id"),
    "gloss": ("gloss",),
    "pos": ("pos",),
    "sent_en": ("sent_en",),
    "rank": ("rank",),
    "keep": ("keep",),
    "reason": ("reason",),
    "hint": ("hint",),
}

CANONICAL = list(ALIASES)
REQUIRED = ("rank", "term", "headword", "gloss")


def get(item, field, default=""):
    """Read a canonical field, accepting the legacy Hindi key as a fallback."""
    fallback = None
    for key in ALIASES[field]:
        if key not in item or item[key] is None:
            continue
        value = item[key]
        # A present-but-EMPTY canonical key must not hide a populated legacy one:
        # a half-converted record would otherwise ship a blank card rather than
        # fall back to the value it actually has.
        if isinstance(value, str) and not value.strip():
            if fallback is None:
                fallback = value
            continue
        return value
    return fallback if fallback is not None else default


def rank(item):
    """Rank as an int, or None when absent/unparseable (never raises)."""
    try:
        return int(get(item, "rank", None))
    except (TypeError, ValueError):
        return None


def missing_fields(item):
    """Which required fields this record cannot supply, under either naming."""
    out = []
    for field in REQUIRED:
        if field == "rank":
            if rank(item) is None:
                out.append("rank")
        elif not str(get(item, field)).strip():
            out.append(field)
    return out


def has_sentence(item):
    """One rule for 'this card has an example sentence', used everywhere.

    v1 disagreed with itself: add_notes wrote a [sound:] reference whenever the
    DISPLAY sentence existed, while audio.py generated the clip only when the
    NATIVE sentence existed — so a lopsided record shipped a play button that
    pointed at a file nobody ever made. Both halves must be present.
    """
    return bool(str(get(item, "sent_native")).strip()) and bool(
        str(get(item, "sent_display")).strip()
    )
