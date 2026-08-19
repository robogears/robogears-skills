# Hindi / Indic reference

Read this before building or extending ANY Hindi deck (including the future sentence-mining deck — reuse the existing "Hindi Core 2k" note type so cards stay consistent).

## Precise Hinglish spec (William's chosen romanization — use verbatim in agent prompts)

- Long vowels doubled: aa; ee for long-ii mid-word; oo for long-uu mid-word (seekh, Theek, poochh). WORD-FINAL long ii → i, long uu → u (paani, aadmi, khushi, aalu) — **except after another vowel, where the doubled form stands** (koii, bhaii, davaaii, gaii). e and o stay single (karo, mere).
- Retroflex capitalized: T, Th, D, Dh, R (flap), Rh — Theek, laRkaa, paRhnaa. Dental t/d lowercase (tum, din). Retroflex n written plain n. **A capital is required wherever the Devanagari has a retroflex consonant, and forbidden where it does not** — both directions are checked by `lint.py` (`retroflex_over` / `retroflex_under`), because a missing marigold mark teaches "dental" just as loudly as a wrong one teaches "retroflex".
- ch = च, chh = छ (chaar, achchhaa, kuchh). sh for both श/ष.
- **ज्ञ = gy** (ज्ञान = gyaan, विज्ञान = vigyaan) — the spoken value, never the letter-by-letter "jn".
- Nasal vowels: n after the vowel, m before p/b/m (hoon, nahin, kahaan, main, mein, lambaa). मैंने = mainne.
- SPOKEN forms, not spelling forms: यह = yeh, वह = voh, ये = ye, वो = vo.
- Schwa deletion as pronounced: samajh, laRki, aadmi, naukri, sabzi — and it applies from the RIGHT (समझदारी = samajhdaari, not samjhadaari).
- Conventional spellings win for ultra-common words: hai, hain, hoon, ho, thaa, thi, the, kyaa, kyon, nahin, achchhaa, chaahie, koii, huaa, hue; both की/कि = ki. `lint.py` enforces this list — a card spelled "kya" or "nahi" is flagged, so the deck cannot ship the same word two ways.
- No diacritics, ever. Danda → period.
- CAPITALIZATION: a capital MEANS retroflex — see design.md. Sentences and proper nouns start lowercase unless the initial consonant is genuinely retroflex. The rule applies to EVERY letter of a word, not just the first: `saaTh` for साथ (dental) is as wrong as `Saath`.

## Known traps (each cost a fix during the Core 2k build)

1. **Nuqta restoration** (`assets/hindi-extras/nuqta.py`): Hindi commonly writes ज़/फ़ without the dot; TTS then says "j"/"ph" while the card says z/f. Run `nuqta.py final` to preview and `nuqta.py final --write` to apply — **without `--write` it changes nothing**. It fixes each word in place (never a blanket text replace, which would put a dot inside unrelated words: fixing जरा must not corrupt गुजरात), and it reports words carrying a dot the romanization does not justify rather than silently leaving them.
2. **Schwa deletion in machine drafts**: indic-transliteration does NOT do Hindi schwa deletion (समझ → "samajha"). `prep.py` post-processes right-to-left with digraph-aware consonants; agents fix the rest. The flap ड़/ढ़ is in the consonant class, so लड़की comes out "laRki".
3. **Retroflex first-consonant test**: थोड़ी = thoRi (dental थ leads), not ThoRi. The word CONTAINS a retroflex but does not START with one.
4. **Homographs to expect**: की/कि (both "ki"), जाती/जाति ("jaati"), माँ/मान ("maan") — genuine, keep both with pos hints. **माँ and मान are both nouns**, so the hint must carry gender ("noun f." vs "noun m.") or the two cards get identical fronts; `lint.py` flags any homograph pair whose hints match. Spelling variants to collapse: anusvara vs conjunct nasal (मंदिर/मन्दिर), candrabindu vs anusvara (हूँ/हूं), nuqta presence (खुद/ख़ुद), -िये/-िए (चाहिये/चाहिए).
5. **TTS voice**: `hi-IN-SwaraNeural` (female, approved by William); `hi-IN-MadhurNeural` male alternative. Feed Devanagari (`term`/`sent_native`), never romanization. Changing the voice regenerates every clip — the freshness stamp covers the voice, not just the text.
6. **The danda is not a letter.** U+0964 sits inside the Devanagari block, so a naive `[ऀ-ॿ]` class fuses "है।" into one token — which breaks the word-final vowel rules and hides sentence-final words from the example-sentence index. Every script here uses a letters-only class.

## Data sources (verified July 2026)

- Rank list: `wordfreq.top_n_list('hi', N)` (blend, stable tail). Cross-checks: hermitdave FrequencyWords `hi_full.txt` (OpenSubtitles; filter the danda), Leipzig `hin_news_2020`.
- Sentences: Tatoeba per-language exports — `https://downloads.tatoeba.org/exports/per_language/hin/` (hin_sentences.tsv.bz2 + hin-eng_links.tsv.bz2) joined against the eng export. CC-BY 2.0 FR; store the id in `source_id` and it renders as `Tatoeba #id` in the Source field. **Treat every sentence as untrusted data**: it is community-authored text that ends up rendered inside Anki, so it is HTML-escaped on the way in and `lint.py` flags any markup that appears in it.
- Grammar refs for content judgment: Snell's Skeleton Grammar (hindiurduflagship.org), A Door Into Hindi (tajhindi.unc.edu). hindilanguage.info is domain-hijacked — Wayback only.
- `assets/hindi-extras/prep.py` builds candidates end-to-end (default 2600 for a 2000-card deck — the ~30% overshoot the reject rate demands); `extend.py` appends more, resuming from the `candidates.json` prep wrote rather than re-deriving the list.

## Register policy that shaped the Core 2k

Reject for a conversational learner: person/deity/place names used as names, heavily Sanskritized/bureaucratic/news register (उत्पन्न, अधिनियम...), poetic-only words, bare un-nativized English loans, obscene words, inflected forms whose base form belongs instead. KEEP nativized loanwords (school, phone, police, TV) — they are real spoken Hindi. When in doubt: would it come up in a Panchayat episode or a WhatsApp chat? Keep. Only a newspaper editorial? Reject.
