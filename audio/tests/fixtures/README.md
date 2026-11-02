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
