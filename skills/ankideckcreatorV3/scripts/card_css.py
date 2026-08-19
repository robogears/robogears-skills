"""Card styling for the Hindi Core 2k deck.

Design notes
------------
Signature: the shirorekha. In Devanagari every letter hangs from a horizontal
headline; here the Hinglish word hangs from that same rule. It is the one
decorative device and it is true to the script the learner is working toward.

Colour code: marigold marks retroflex consonants (T Th D Dh R Rh) and nothing
else, so "gold = curl the tongue back" becomes learnable by exposure alone.
The target word inside the example sentence is marked by weight and a hairline
underline instead, so the two signals never compete.

Type: Avenir Next (geometric-humanist, unambiguous capitals — critical when a
capital letter carries phonetic meaning) against Iowan Old Style for English
glosses, and Kohinoor Devanagari for the script line.
"""

CSS = """
:root {
  --paper: #EFEDE6;
  --panel: #F7F6F1;
  --ink: #1C2C4C;
  --ink-soft: #55637F;
  --ink-faint: #97A0B4;
  --rule: rgba(28, 44, 76, 0.28);
  --gold: #B07A16;
  --hair: rgba(28, 44, 76, 0.12);
}
.nightMode, .card.night_mode {
  --paper: #12151E;
  --panel: #191D28;
  --ink: #E7E4DA;
  --ink-soft: #9AA2B4;
  --ink-faint: #5E6678;
  --rule: rgba(231, 228, 218, 0.30);
  --gold: #DFAE55;
  --hair: rgba(231, 228, 218, 0.14);
}

.card {
  background: var(--paper);
  color: var(--ink);
  font-family: "Iowan Old Style", "Charter", Palatino, Georgia, serif;
  font-size: 17px;
  text-align: center;
  padding: 0;
  margin: 0;
  -webkit-font-smoothing: antialiased;
}

.wrap {
  max-width: 33em;
  margin: 0 auto;
  padding: 5vh 1.25rem 3vh;
  box-sizing: border-box;
}

/* frequency rank — quiet, but true: how common this word actually is */
.rank {
  font-family: "Avenir Next", Avenir, ui-sans-serif, system-ui, sans-serif;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin-bottom: 2.6rem;
}

/* the shirorekha: the word hangs from its headline */
.head { display: inline-block; border-top: 1.5px solid var(--rule); padding: 0.62rem 0.9rem 0; }
.word {
  font-family: "Avenir Next", Avenir, ui-sans-serif, system-ui, sans-serif;
  font-size: 2.65rem;
  font-weight: 500;
  letter-spacing: 0.005em;
  line-height: 1.16;
  color: var(--ink);
}
.head.small { border-top-color: var(--hair); padding-top: 0.5rem; }
.head.small .word { font-size: 1.6rem; }

/* marigold = retroflex. the only place this colour appears. */
.rx { color: var(--gold); }

/* shown only on the handful of words that romanize identically — part of
   speech is enough to say which one is being asked for */
.hint {
  font-family: "Avenir Next", Avenir, ui-sans-serif, system-ui, sans-serif;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.11em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin-top: 1.1rem;
}

.gloss { font-size: 1.5rem; line-height: 1.4; margin-top: 1.5rem; color: var(--ink); }
.pos {
  font-family: "Avenir Next", Avenir, ui-sans-serif, system-ui, sans-serif;
  font-size: 11.5px;
  font-weight: 500;
  letter-spacing: 0.13em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin-top: 0.6rem;
}

.sentence {
  margin-top: 2.3rem;
  padding: 1.5rem 1.3rem 1.4rem;
  background: var(--panel);
  border-radius: 3px;
}
.sent-hi {
  font-family: "Avenir Next", Avenir, ui-sans-serif, system-ui, sans-serif;
  font-size: 1.32rem;
  font-weight: 400;
  line-height: 1.62;
  color: var(--ink);
}
/* the target word in the wild: weight + hairline, never colour */
.tw { font-weight: 600; border-bottom: 1px solid var(--rule); padding-bottom: 1px; }
.sent-en { font-size: 1.05rem; line-height: 1.5; color: var(--ink-soft); margin-top: 0.7rem; }

.script {
  font-family: "Kohinoor Devanagari", "Devanagari Sangam MN", "Noto Sans Devanagari", serif;
  font-size: 1rem;
  line-height: 1.85;
  color: var(--ink-faint);
  margin-top: 2.1rem;
  padding-top: 1.1rem;
  border-top: 1px solid var(--hair);
}
.script .sep { padding: 0 0.5rem; opacity: 0.5; }

.replay-button { margin-top: 1.1rem; }
.replay-button svg { width: 26px; height: 26px; }
.replay-button svg circle { fill: transparent; stroke: var(--rule); }
.replay-button svg path { fill: var(--ink-soft); }
.sentence .replay-button { margin-top: 0.9rem; }

@media (max-width: 420px) {
  .wrap { padding: 3.5vh 0.9rem 2vh; }
  .word { font-size: 2.15rem; }
  .gloss { font-size: 1.3rem; }
  .sent-hi { font-size: 1.16rem; }
  .rank { margin-bottom: 1.9rem; }
}
"""

# The Rank FIELD carries the whole phrase ("147 of 2000"), written by
# add_notes.py from config.TOTAL. v1 stored the phrase AND appended " of 2000"
# here, so every card read "147 of 2000 of 2000" — and the hardcoded 2000
# contradicted the configured deck size on any other deck.
FRONT = """<div class=wrap>
<div class=rank>{{Rank}}</div>
<div class=head><div class=word>{{Hinglish}}</div></div>
{{#Hint}}<div class=hint>{{Hint}}</div>{{/Hint}}
</div>"""

BACK = """<div class=wrap>
<div class=rank>{{Rank}}</div>
<div class="head small"><div class=word>{{Hinglish}}</div></div>
{{WordAudio}}
<div class=gloss>{{English}}</div>
<div class=pos>{{Notes}}</div>
{{#Sentence}}<div class=sentence>
<div class=sent-hi>{{Sentence}}</div>
<div class=sent-en>{{SentenceEnglish}}</div>
{{SentenceAudio}}
</div>{{/Sentence}}
{{#Devanagari}}<div class=script>{{Devanagari}}{{#SentenceDevanagari}}<span class=sep>&middot;</span>{{SentenceDevanagari}}{{/SentenceDevanagari}}</div>{{/Devanagari}}
</div>"""
