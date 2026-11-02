# Fixtures

## lane_a/synthetic_cat.json

Hand-written `PreparedPassage` and `LaneAResult` for the passage "The cat sat on the big mat". Keys: `passage`, `lane_a`. Units cover:

| Unit | Case |
|---|---|
| the | correct |
| cat | substitution (read as "cap") |
| sat | correct, no timing (`verifiable == False`) |
| on | omission (empty surface, no timing) |
| (insertion "very") | insertion (`passage_index` None, span 0) |
| the | correct |
| big | mispronunciation (non-word "bik") |
| mat | correct |

The trace and ASR word list are consistent with the units.

## passages/*.txt

Six **made-up placeholder** passages (60–120 words, Grade 1–3 level), named after the file-name prefixes of the recordings in `audio/data/`. They are **not** the texts the recordings read. Real texts must replace them before any real-recording fixture or calibration (B6), and every Filipino word in the real texts must be added to the lexicon. Tests rely only on each passage's opening phrase.

Between them they cover a digit, contractions, a possessive, hyphenated compounds, heteronyms in context (read, live), a dash token, curly apostrophes (`nick_is_a_tri`) and Filipino names and words from the lexicon.

## g2p_golden.yaml

espeak-ng output (en-us, normalised) for 70 common words, a regression check against espeak version drift. Generated once and reviewed by hand; the espeak version is in the header.
