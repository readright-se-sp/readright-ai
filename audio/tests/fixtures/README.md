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

The **real passage texts** the recordings read, byte for byte (UTF-8, curly quotes kept). Source: the Supabase passages export of 2026-11-02 (one `text` column, 8 rows, no titles; row 8 duplicates row 4 and is skipped). They are copies of `docs/lane-b-context/passages/`. File names follow the recording prefixes in `audio/data/` (see `devtools/recordings.py`); `anansi.txt` has no recording and is extra Stage 0 coverage.

The texts have no digit, hyphen or dash, and no straight-apostrophe contraction. Those cases are covered by inline strings in `test_passage.py`.

## g2p_golden.yaml

espeak-ng output (en-us, normalised) for 70 common words, a regression check against espeak version drift. Generated once and reviewed by hand; the espeak version is in the header.
