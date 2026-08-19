"""Turn a plain display headword/sentence into card markup.

Two marks, each with exactly one job:
  .rx  marigold on retroflex capitals (T Th D Dh R Rh) — a pronunciation code
  .tw  weight + hairline on the target word inside its example sentence
Applied per word token so no regex ever touches generated HTML.

Text is HTML-ESCAPED before any markup is generated (see escape). v1 escaped
nothing, so a sentence carrying `<img src=x onerror=...>` — Tatoeba is a
community corpus — reached Anki's renderer as live HTML. Order matters: escape
first, THEN add our own tags, or the tags get escaped too.
"""
import html
import re
import unicodedata

RETRO_RE = re.compile(r"[TDR]h?")
# Devanagari LETTERS only. The block also contains the danda (U+0964), the
# double danda (U+0965) and the digits U+0966-096F; v1 treated all of them as
# letters, which fused sentence-final words with their full stop and broke both
# the romanization rules and the roman/native word alignment below.
DEV_RE = re.compile(r"[ऀ-ॣ॰-ॿ]")
# The retroflex series in Devanagari — the ground truth for a capital letter.
# Bare base letters only: ड़/ढ़ decompose to ड/ढ + nuqta under NFD, so they are
# covered, and listing them literally would smuggle the nuqta into the set.
# ण is deliberately ABSENT: the spec writes retroflex n as a plain "n", so it
# has no roman marker to compare against. Counting it made lint demand a capital
# for every कारण/गुण/क्षण in the deck.
RETRO_DEV = set("टठडढ")
# ...but ण IS retroflex for the "does this word START retroflex" question,
# which is about the sound, not about the marker.
RETRO_DEV_SOUND = RETRO_DEV | set("ण")

CONSONANTS = set(chr(c) for c in range(0x0915, 0x093A))


def escape(text):
    """HTML-escape text that will be placed in an Anki field."""
    return html.escape(str(text or ""), quote=False)


def _first_consonant(dev_word: str) -> str:
    for ch in dev_word:
        if ch in CONSONANTS:
            return ch
    return ""


def _normalize(dev_word: str) -> str:
    """Decompose, so a precomposed ड़ (U+095C) is seen as ड + nuqta.

    Without this the counters below score a precomposed flap as zero retroflex,
    which both invents a missing-mark warning and lets _demote_unjustified
    lowercase a capital the script genuinely justifies.
    """
    return unicodedata.normalize("NFD", dev_word or "")


def _is_retroflex_dev(dev_word: str) -> bool:
    """Is the word's FIRST consonant retroflex?

    Asking whether the word contains any retroflex letter is not the same
    question and gets थोड़ी wrong: its ड़ is retroflex but its leading थ is a
    dental, so 'ThoRi' would falsely teach a curled tongue on the first sound.
    """
    return _first_consonant(_normalize(dev_word)) in RETRO_DEV_SOUND


def count_retroflex_dev(dev_word: str) -> int:
    """How many retroflex consonants the native form actually contains."""
    return sum(1 for ch in _normalize(dev_word) if ch in RETRO_DEV)


def count_retroflex_marks(roman_word: str) -> int:
    """How many retroflex markers the romanization claims (T/Th/D/Dh/R/Rh)."""
    return len(RETRO_RE.findall(roman_word))


def native_words(sent_native: str):
    """Native words for alignment: split on whitespace AND hyphens, letters only.

    Both fixes matter. Dropping danda/digit-only tokens stops a spaced full stop
    from inventing an extra 'word'; splitting hyphens matches the roman side,
    where `Thiik-Thaak` is already two tokens. With v1's counting those two
    errors could cancel out, produce a false alignment, and lowercase a GENUINE
    retroflex — teaching a dental where the script says retroflex.
    """
    parts = re.split(r"[\s‐-―\-]+", sent_native or "")
    return [w for w in parts if DEV_RE.search(w)]


def _demote_unjustified(tok: str, native: str) -> str:
    """Lowercase only capitals the native form certainly cannot justify."""
    no_retroflex_at_all = count_retroflex_dev(native or "") == 0
    out = list(tok)
    for j, ch in enumerate(out):
        if not ch.isupper():
            continue
        if j == 0:
            if ch not in "TDR" or not _is_retroflex_dev(native or ""):
                out[j] = ch.lower()          # not a marker, or not retroflex
        elif ch in "TDR" and no_retroflex_at_all:
            out[j] = ch.lower()              # word has no retroflex anywhere
        # A mid-word capital outside T/D/R claims nothing about pronunciation
        # (acronyms like TV keep theirs); lint.py reports it for review.
    return "".join(out)


def normalize_caps(sent_roman: str, sent_native: str) -> str:
    """Strip capitals that mean 'start of sentence' rather than 'retroflex'.

    In this romanization a capital T/D/R encodes a curled tongue, so ordinary
    English capitalization would make the code lie. Any capital the native
    script does not back up gets lowercased.

    Unlike v1 this inspects EVERY letter of a word, not just the first: a
    mid-word `saaTh` for dental साथ used to sail through untouched and render
    with a marigold 'curl your tongue' mark. Only certainly-unjustified capitals
    are lowered here (the native word has no retroflex consonant at all);
    ambiguous count mismatches are left alone and reported by lint.py, which is
    the honest split — never guess which of two capitals to demote.
    """
    if not sent_roman:
        return sent_roman
    tokens = re.split(r"([A-Za-z]+)", sent_roman)
    words = native_words(sent_native)
    roman_idx = [i for i in range(len(tokens)) if i % 2 == 1]
    aligned = len(words) == len(roman_idx)

    for n, i in enumerate(roman_idx):
        tok = tokens[i]
        if not any(c.isupper() for c in tok):
            continue
        if not words:
            # No native text at all, so nothing can justify a capital. The
            # romanization's default is lowercase, so demote rather than let an
            # unbacked 'Tum' render as a marigold retroflex claim.
            native = ""
        elif aligned:
            native = words[n]
        elif n == 0:
            native = words[0]
        else:
            # Alignment failed and this is not the opening word: we cannot tell,
            # so leave the builder's choice alone. lint.py reports these.
            continue
        tokens[i] = _demote_unjustified(tok, native)
    return "".join(tokens)


def mark_retroflex(word: str) -> str:
    """Wrap retroflex markers in the marigold span. Input must be escaped."""
    return RETRO_RE.sub(lambda m: f'<span class=rx>{m.group(0)}</span>', word)


def normalize_headword(headword: str, native: str) -> str:
    """Drop capitals on a headword unless the native script justifies them.

    Proper nouns included: 'Hindi' and 'Amerikaa' would otherwise read as if
    they began with a retroflex consonant. Mid-word capitals get the same
    treatment as in sentences.
    """
    if not headword:
        return headword
    return _demote_unjustified(headword, native or "")


def _stem_match(head: str, word: str) -> int:
    """Score how well a sentence word matches the headword. Higher is better."""
    h, w = head.lower(), word.lower()
    if h == w:
        return 1000
    common = 0
    for a, b in zip(h, w):
        if a != b:
            break
        common += 1
    # The shared prefix must be nearly ALL of the shorter word. v1 asked only
    # that it be long enough in absolute terms, which both missed real
    # inflections ('aanaa'/'aae' — target left un-bolded) and admitted false
    # ones ('pataa'/'patthar' — the WRONG word bolded, mislabelling what the
    # headword means). Measuring against the shorter word separates them:
    # aanaa/aae shares its whole stem, pataa/patthar diverges early.
    # Bolding is still best-effort; target_in_sentence() below reports the
    # cards where nothing matched so lint can surface them rather than a
    # looser guess quietly marking the wrong word.
    # Two acceptance rules, and a match needs only ONE. Replacing v1's rule with
    # the second one alone looked cleaner but lost a dozen ordinary conjugations
    # (karnaa/karke and friends), which is a worse trade than the false match it
    # avoided: a missing bold is cosmetic, and lint now reports it either way.
    need = 3 if len(h) <= 5 else 4
    if common >= need and common >= len(h) - 4:
        return common
    # short shared stems that are nearly all of BOTH words: aanaa/aae, ho/hoon
    if common >= 2 and common >= min(len(h), len(w)) - 1:
        return common
    return 0


def render_sentence(sent: str, headword: str, accent: bool = True) -> str:
    """Escape, mark retroflex letters, and bold the target word once.

    `accent=False` (a deck with no pronunciation code) keeps the target-word
    bolding and skips the marigold entirely.
    """
    if not sent:
        return ""
    tokens = re.split(r"([A-Za-z]+)", sent)
    # A headword can be a multi-word pattern ("kartaa hoon", "carbon dioxide").
    # Scoring the whole string against a single token never matches, which left
    # every such card with nothing marked, so each part gets its own shot and
    # the best-matching single token wins.
    parts = [p for p in re.split(r"[^A-Za-z]+", headword or "") if p]
    best_idx, best_score = -1, 0
    for i, tok in enumerate(tokens):
        if i % 2 == 1:
            s = max((_stem_match(p, tok) for p in parts), default=0)
            if s > best_score:
                best_idx, best_score = i, s
    out = []
    for i, tok in enumerate(tokens):
        piece = escape(tok)
        if i % 2 == 1:
            if accent:
                piece = mark_retroflex(piece)
            if i == best_idx:
                piece = f'<b class=tw>{piece}</b>'
        out.append(piece)
    return "".join(out)


def render_headword(headword: str, accent: bool = True) -> str:
    """Escape a headword and colour its retroflex markers."""
    piece = escape(headword)
    return mark_retroflex(piece) if accent else piece


def target_in_sentence(sent_display: str, headword: str) -> bool:
    """Does the DISPLAYED sentence actually contain the target word?

    v1 checked only the native side, and only by raw substring, so a card could
    ship with nothing bolded and no warning.
    """
    if not sent_display or not headword:
        return False
    return any(_stem_match(headword, tok) > 0
               for tok in re.findall(r"[A-Za-z]+", sent_display))
