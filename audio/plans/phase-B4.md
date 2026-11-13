# Phase B4 plan — Compare (weighted phoneme distance, accent table)

Status: approved (open questions: recommendations accepted). Planning happens in one session and implementation in another. The implementing session follows this file and [../ROADMAP.md](../ROADMAP.md) "How to work a phase" steps 4–5. It does not change decisions here; if something does not work, it stops and reports.

## Goal

Spec §5.6 step 3 and §6. For one unit, compute how far the heard phonemes are from each expected variant and from the ASR word's pronunciation. Use a weighted edit distance in which Philippine English accent features cost almost nothing, so accent is never scored as a miscue (a Phil-IRI requirement, §6.1).

B4 gives B5 three things: `compare()`, the expected variants per unit, and the hypothesis variants per unit. B5 builds the `WordVerdict`s. Thresholds (`T_FLAG`, `T_OVERRULE`) belong to B5.

## Files

| File | Change |
|---|---|
| `pyproject.toml` | wheel `include` adds `phonetics/data/*.json`. No new dependency |
| `src/audio/phonetics/ipa.py` | add `VOWELS` |
| `src/audio/phonetics/data/accent_table.yaml` | new: cost constants and accent pairs |
| `src/audio/phonetics/accent_table.py` | new: loader |
| `devtools/build_feature_distances.py` | new: generates the table with panphon (build time only) |
| `src/audio/phonetics/data/feature_distances.json` | new: generated, committed |
| `src/audio/phonetics/features.py` | new: table lookup |
| `src/audio/lane_b/compare.py` | new: `compare`, `unit_variants`, `hypothesis_variants` |
| `src/audio/types.py` | add `Comparison` under "Lane B intermediate" |
| `tests/test_accent_table.py`, `tests/test_features.py`, `tests/test_compare.py` | new |
| `ROADMAP.md`, `../README.md` | status, decisions |

If the generator cannot run, stop and report.

## `ipa.py`: `VOWELS`

`VOWELS: frozenset[str]` holds the vowels and diphthongs in `INVENTORY`: `a i o u æ ɑ ɔ ə ɛ ɜ ɪ ʊ ʌ ɚ eɪ aɪ ɔɪ aʊ oʊ`. Add a test that `VOWELS <= INVENTORY`. A consonant is any `INVENTORY` symbol not in `VOWELS`; `n̩` counts as a consonant.

## `accent_table.yaml`

```yaml
# Spec §6. Version-controlled data; B6 calibrates the numbers.
costs:
  accent: 0.1               # an accent pair below (spec 6.4: θ→t is low, not zero)
  final_cluster_drop: 0.2   # deleting the last consonant of a word-final cluster (test → tes)
  substitution_floor: 0.4   # any other substitution: floor + (1 − floor) × feature distance
  indel: 1.0                # any other insertion or deletion
# [expected, heard]. One direction only; list both directions when both apply.
pairs:
  # spec 6.2
  - [f, p]
  - [v, b]
  - [θ, t]
  - [ð, d]
  - [z, s]
  - [ʒ, ʃ]
  # spec 6.2 additional features
  - [ɪ, i]
  - [i, ɪ]
  - [ʊ, u]
  - [u, ʊ]
  - [æ, a]
  - [æ, ɛ]
  # reduced schwa: ə heard as a full vowel
  - [ə, a]
  - [ə, ɛ]
  - [ə, e]
  - [ə, i]
  - [ə, ɪ]
  - [ə, o]
  - [ə, ɔ]
  - [ə, u]
  - [ə, ʊ]
  - [ə, ʌ]
  - [ə, ɑ]
  - [ə, æ]
  # beyond the spec table (open question 2): Philippine English monophthongs and tapped r
  - [eɪ, e]
  - [eɪ, ɛ]
  - [oʊ, o]
  - [ɹ, ɾ]
```

`accent_table.py`: `load_accent_table()`, cached with `functools.cache`, returns `AccentTable(costs, pairs: frozenset[tuple[str, str]])` (a frozen pydantic model or a NamedTuple). Validation, which raises `ValueError` when it fails:
- all four costs are present, each in `[0, 1]`
- every expected symbol is in `INVENTORY`
- every heard symbol is non-empty with no whitespace (it can be a recogniser symbol outside `INVENTORY`, such as `e`)
- there are no duplicate pairs

## `features.py` and the feature-distance table

**Amendment (2026-11-02):** panphon fails to import on Windows (it reads its UTF-8 CSV with the cp1252 default) and pulls in pandas and five other packages. So panphon is used **once, at build time**, and is **not** a project dependency. Revert the `panphon` change to `pyproject.toml` and `uv.lock`.

**Generator: `devtools/build_feature_distances.py`.** Run it from `audio/` with:

```
PYTHONUTF8=1 uv run --with panphon python devtools/build_feature_distances.py
```

(PowerShell: `$env:PYTHONUTF8=1` first.) Put this command in the module docstring.
- Symbols: `INVENTORY`, plus every segment the chosen recogniser's vocabulary maps to. Get these by loading the tokenizer vocabulary of `AudioSettings().phoneme_model` the way `recognition/phoneme_recognizer.py` does, then running it through `build_token_map`. The weights are in the HF cache.
- raw = `panphon.distance.Distance().weighted_feature_edit_distance(a, b)`. It handles multi-segment symbols (diphthongs, `tʃ`) as short strings.
- If panphon does not fully recognise a symbol (`"".join(FeatureTable().ipa_segs(sym)) != sym`), use `_ALIASES` in the generator, mapping it to a spelling panphon knows (for example `ɚ → ə˞`). If no alias fits a symbol, leave it out of the table and print it.
- `R` = the largest raw value over all unordered pairs of single-character `INVENTORY` symbols. scaled = `min(1.0, raw / R)`, rounded to 4 decimals.
- Write `src/audio/phonetics/data/feature_distances.json` as `{"scale_R": R, "symbols": [...], "distances": {"a": {"b": 0.1234, ...}, ...}}`. Store the upper triangle only (`a < b` by code point), with sorted keys and `ensure_ascii=False`, so reruns give the same diff. Commit the JSON and the generator.

**Runtime: `phonetics/features.py`.** It uses only the stdlib.
- `feature_distance(a: str, b: str) -> float`: 0 when `a == b`; otherwise look up the pair in either order.
- A symbol not in the table gives 1.0 and is logged once with `logger.warning`. Rerun the generator when that shows up.
- Load the JSON once (`functools.cache`). The `pyproject.toml` wheel `include` must also pick up `*.json` in `phonetics/data/`.

## `compare.py`

```python
def compare(heard: Sequence[str], variants: Sequence[PhoneSeq]) -> Comparison | None
```

Weighted Levenshtein of `heard` against each non-empty variant. It returns `None` when there is no non-empty variant.
- Match: 0.
- Substitution of `e` by `h`: `accent` if `(e, h)` is a pair, else `substitution_floor + (1 − substitution_floor) × feature_distance(e, h)`.
- Deletion of `expected[j]`: `final_cluster_drop` when `j` is the last index, `j ≥ 1`, and `expected[j]` and `expected[j − 1]` are both consonants; else `indel`.
- Insertion: `indel`.
- `distance = cost / len(variant)`. The best variant is the lowest distance; on a tie, the lower index wins.
- Backtrace from the end cell. On equal cost, prefer diagonal (match or substitution), then deletion, then insertion. This is spec `TIE_BREAK` "prefer pairing". Fixed rule, tested.
- `deviations`: in expected order, `(expected, heard)` for substitutions, `(expected, "")` for deletions and `("", heard)` for insertions. Leave out matches, accent-pair substitutions and final-cluster drops.

```python
class Comparison(_Model):  # types.py
    distance: Distance
    best_variant_index: Annotated[int, Field(ge=0)]
    deviations: tuple[Deviation, ...]
```

```python
def unit_variants(unit: Unit, passage: PreparedPassage) -> tuple[PhoneSeq, ...]
```

Expected variants for a unit: `()` for an insertion (`passage_index is None`). Otherwise take the Cartesian product of `passage.pronunciations[token.key]` over the unit's `passage_span` tokens, with each product concatenated. Keep the first 16 in `itertools.product` order (`# ponytail: cap 16; raise if multi-token units with many variants show up`).

```python
def hypothesis_variants(units: Sequence[Unit], preparer: PassagePreparer) -> tuple[tuple[PhoneSeq, ...], ...]
```

The pronunciations of what the ASR heard, through the same Stage 0 path (lexicon, then possessive, then espeak plus variants):
- one `preparer.prepare(" ".join(u.surface for u in units if u.surface))` call
- then for each unit, `tokenise(unit.surface)` → `interim_key` → that token's pronunciations, with the product over tokens capped at 16 as above
- `()` for a unit whose surface has no tokens

On `PassagePreparationError`, log a warning and return `()` for every unit; B5 then has no `distance_hypothesis` (`# ponytail: all-or-nothing; per-word fallback if this happens on real data`). The caller passes a separate `PassagePreparer` with `cache_size=0`, so hypothesis texts do not push real passages out of the passage cache. B7 wires that up.

## Tests

No espeak and no model. `hypothesis_variants` uses a fake `G2P` (the `G2P` protocol in `g2p.py`).

`test_accent_table.py`
1. The real YAML loads. It contains the six spec 6.2 pairs and `(θ, t)`.
2. The loader rejects: an unknown expected symbol, a cost outside `[0, 1]`, a missing cost, a duplicate pair. Use `tmp_path` YAML, so give the loader a path argument whose default is the packaged file.

`test_features.py`
3. The table's symbols include every `INVENTORY` symbol, and its `scale_R` is > 0.
4. `feature_distance(x, x) == 0`, it is symmetric, and it stays in `[0, 1]` over all `INVENTORY` pairs.
5. Sanity: `feature_distance("p", "b") < feature_distance("p", "a")`.
6. An unknown symbol (`"☃"`) gives 1.0.

`test_compare.py`
7. Identical sequences: distance 0, no deviations.
8. Parametrised over every YAML pair, with a 3-segment carrier: expected `[k, X, t]`, heard `[k, Y, t]` for pair `(X, Y)`: distance `accent / 3`, no deviations.
9. A one-way pair reversed (expected `p`, heard `f`) costs a full substitution and is a deviation.
10. cat → cap (`k æ t` vs `k æ p`): deviation `[("t", "p")]`, distance ≥ `substitution_floor / 3`.
11. fink for think (`θ ɪ ŋ k` vs `f ɪ ŋ k`): deviation `[("θ", "f")]`, distance > `accent / 4`.
12. Final cluster: `t ɛ s t` heard as `t ɛ s` costs `final_cluster_drop / 4` with no deviation. A dropped final consonant after a vowel (`k æ t` → `k æ`) costs `indel / 3` and is a deviation. A dropped middle consonant is `indel`.
13. Insertion and deletion deviations use `""` on the missing side.
14. Best variant: the second variant wins when it is closer. Equal distances pick index 0.
15. Empty `heard`: all deletions, which is the final-cluster rule when it applies, else `indel`.
16. No non-empty variant gives `None`.
17. The tie-break is deterministic: `a a` vs `a` gives `[("a", "")]`, and the same input twice gives the same backtrace.
18. `unit_variants`: an insertion gives `()`; `passage_span=2` with 2 × 2 variants gives 4 concatenations in product order; the cap is 16.
19. `hypothesis_variants` with a fake G2P: lexicon words take the lexicon path; an empty surface gives `()`; a G2P error gives `()` for every unit.

## Done when

- `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .` pass. Nothing new is skipped.
- ROADMAP:
  - the B4 row says Done with a link to this plan
  - in the B4 section, the accent line reads "ə → full vowel" (spec direction) and lists the extra pairs and the substitution floor
  - the decisions below are recorded
- Root README "Current state" mentions compare.
- One Conventional Commit on `feature/lane-b` including this plan, for example `feat(audio): add weighted phoneme compare with accent table (B4)`.
  - No Claude or co-author trailers.
  - Nothing from `audio/data/`, `audio/devtools/out/` or `.env` is staged.
  - No push.

## Open questions (recommended answers; approve or change)

1. **θ→t (spec 6.4).** **Recommend:** the same low cost as every accent pair (0.1), not zero. B6 decides from data. Record it as an SDD item for the team.
2. **Pairs beyond the spec table:** `eɪ→e/ɛ` and `oʊ→o` (Philippine English monophthongs; the B2 spike saw the recogniser emit `e` 16 times), and `ɹ→ɾ` (tapped r, already used in the lexicon). Every missing pair turns a correct word into a miscue (§6.4). **Recommend:** include them, marked "beyond spec" in the YAML. B6 validates.
3. **Substitution floor.** Raw feature distances for close consonants (`t`/`p`) are small, so without a floor cat→cap would look almost like an accent pair. **Recommend:** `0.4 + 0.6 × feature distance`, as data in the YAML for B6 to tune.
4. **Schwa direction.** The ROADMAP says "full vowel → ə"; the spec says ə is *realised as* a full vowel. **Recommend:** follow the spec (expected `ə`, heard a full vowel) and fix the ROADMAP text.
5. **panphon at runtime or a precomputed table.** Decided by the amendment: a precomputed table, generated with panphon at build time. There is no runtime dependency, and Windows works.
6. **Hypothesis pronunciations.** **Recommend:** reuse `PassagePreparer` (same lexicon, possessive and espeak path) on the joined surfaces, with a separate instance with `cache_size=0`. No second G2P path.

## Decisions (approved with this plan)

1. Costs and accent pairs live in `phonetics/data/accent_table.yaml`. Accent pairs are directional `[expected, heard]`.
2. Substitution cost: accent pair → `accent`; otherwise `floor + (1 − floor) × feature distance` from the committed table, generated with panphon. Insertion and deletion → `indel`, except a final-cluster drop.
3. Accent pairs and final-cluster drops are left out of `deviations`.
4. Backtrace ties prefer diagonal, then deletion, then insertion.
5. Multi-token units use the product of per-token variants, capped at 16.
6. Hypothesis pronunciations come from a non-caching `PassagePreparer`; a G2P failure disables `distance_hypothesis` for the request.
