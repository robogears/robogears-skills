# Design reference — why the cards look the way they do

William approved this design for his Hindi Core 2k and asked for every future deck to match it exactly. `scripts/card_css.py` is the source of truth; this file explains the intent so adaptations (when a new deck genuinely needs one) preserve meaning instead of just pixels.

## The elements and what they mean

- **The hanging rule (shirorekha).** The headword hangs beneath a thin horizontal rule — an echo of the headline every Devanagari letter hangs from. It is the single decorative device on the card. For non-Indic decks it stays: it reads as the house signature now.
- **Marigold = the deck's one learnable code.** In Hindi it marks retroflex consonants (`<span class=rx>` on capital T/Th/D/Dh/R/Rh): gold means "curl the tongue back". The color appears NOWHERE else, so the learner absorbs the code passively. A new deck may reassign what marigold marks (e.g. tones, irregular forms, the stressed syllable) but it must mark exactly ONE thing, or nothing. Never use it decoratively.
- **Target word = weight + hairline underline (`<b class=tw>`), never color.** Two signals, two channels; they never compete.
- **Rank line ("147 of 2000").** Quiet but true: tells the learner how common/important this item is. Keep it honest — actual rank, actual total. The whole phrase lives in the `Rank` FIELD, written from `config.TOTAL`; the template renders `{{Rank}}` alone. Exactly one side owns the suffix, or cards read "147 of 2000 of 2000". If the deck is later extended, `refresh_fields.py --only Rank` re-states every card's denominator — a frozen total is a dishonest rank line.
- **Hint line (front, homographs only).** Part of speech ONLY — it disambiguates which word is being asked without leaking the answer. When two homographs share a part of speech (माँ/मान, both nouns), the pos must carry its gender, or an explicit `hint` must; identical hints mean identical fronts, which is worse than no hint at all. `lint.py` flags that case.
- **Sentence panel on the back.** The example sentence is the flip side's centerpiece — William asked for "sentences on the flip" explicitly. Native-script line sits faint at the card's foot: present for future script-learning, never demanding attention.
- **Palette**: parchment `#EFEDE6`, indigo ink `#1C2C4C`, marigold `#B07A16`; dark mode `#12151E` / `#E7E4DA` / `#DFAE55`. **Type**: Avenir Next for headwords (geometric-humanist; unambiguous capitals matter when a capital carries phonetic meaning), Iowan Old Style for glosses/sentence translations, Kohinoor Devanagari (or the script's system font) for native script.

## The capitalization lesson (language decks with a capital-based code)

When capitals carry phonetic meaning, ordinary English capitalization makes the code lie. Hindi sentences therefore start lowercase ("tum kahaan se ho?"), proper nouns are lowercase ("dilli"), and a capital survives only when the native script justifies it ("Theek", "DaakTar"). The test is the word's FIRST CONSONANT, not "contains a retroflex anywhere" — थोड़ी is thoRi, not ThoRi; getting this wrong teaches the wrong consonant. `markup.py:normalize_caps/normalize_headword` implement it. Never "fix" lowercase sentence openings; tell the user it's deliberate so it doesn't read as a bug.

## What never changes

Fonts, palette, the hanging rule, the rank line, sentence-on-the-back, night-mode support, the one-code rule for marigold. If a request seems to demand breaking one of these, ask William first — don't quietly deviate.
