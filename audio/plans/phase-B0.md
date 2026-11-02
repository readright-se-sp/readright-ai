# Phase B0 plan — Project setup and shared types (interim)

Status: approved by the user on 2026-11-02; ready to implement. Planning happens in one session and implementation in another. The implementing session follows this file and [../ROADMAP.md](../ROADMAP.md) "How to work a phase" steps 4–5. It does not change decisions here; if something does not work, it stops and reports.

## Goal

An installable `audio/` uv project with one module of shared, validated types for every hand-off between stages. With it, Lane B phases B1–B5 can be built and tested against fixtures before Lane A code exists. B0 has no ML, no API and no audio processing.

## Data on disk (context only)

`audio/data/` holds 9 recordings: WAV, 16 kHz, 16-bit, mono (already the server's output format), 32–67 s each, covering 6 passages (file name prefixes: `amouseanda`, `everysatur` ×3, `mamawas`, `mariancame` ×2, `nickisatri`, `ringringra`). There are no passage texts and no labels yet. B0 does not use the recordings; it only makes sure they are never committed.

## Files

| File | Action |
|---|---|
| `.gitignore` (repo root) | `audio/data/` is already listed; leave it |
| `audio/pyproject.toml` | new |
| `audio/README.md` | new: purpose, layout, how to install and run tests, link to ROADMAP |
| `audio/src/audio/__init__.py` | new: `__version__ = "0.1.0"` |
| `audio/src/audio/settings.py` | new |
| `audio/src/audio/types.py` | new: all shared types |
| `audio/tests/conftest.py` | new: fixture loader |
| `audio/tests/fixtures/lane_a/synthetic_cat.json` | new |
| `audio/tests/fixtures/README.md` | new: what each fixture covers |
| `audio/tests/test_types.py` | new |
| `audio/tests/test_contract_labels.py` | new |
| `audio/ROADMAP.md` | mark B0 done, record decisions |
| `README.md` (root) | update the `audio/` row in "Current state" |

## `pyproject.toml`

Mirror `server/pyproject.toml`, with these differences:
- `name = "readright-audio"`, `requires-python = ">=3.11,<3.13"`. torch and transformers wheels (B2) lag new Python releases, so the cap avoids a forced downgrade later.
- src layout: `[tool.hatch.build.targets.wheel] packages = ["src/audio"]`, `[tool.hatch.version] path = "src/audio/__init__.py"`, pytest `pythonpath = ["src"]`.
- Dependencies: `pydantic>=2`, `pydantic-settings`. Dev: `pytest`, `pyyaml`, `ruff`. Do not add ML dependencies yet; each phase adds its own.
- Copy the ruff config from the server (line length 100, `py311`, `E F I B UP`).
- Commit `uv.lock`.

## `settings.py`

`AudioSettings(BaseSettings)`, `env_prefix="AUDIO_"`, `.env` support like the server. B0 fields: `log_level: str = "INFO"`, and `data_dir: Path = Path("data")` (the local recordings, used by devtools later). Later phases add their own fields. Add `audio/.env.example` with these two entries.

## `types.py`

Every model is a frozen pydantic v2 model with `extra="forbid"`. Use tuples, not lists, so instances are immutable and hashable. All times are integer milliseconds from the start of the WAV.

```python
PhoneSeq = tuple[str, ...]  # IPA segments, already normalised (no stress, no tie bars)


class WordLabel(StrEnum):
    # values equal the contract MiscueType values, plus "correct"
    CORRECT = "correct"
    MISPRONUNCIATION = "mispronunciation"
    OMISSION = "omission"
    SUBSTITUTION = "substitution"
    INSERTION = "insertion"
    REPETITION = "repetition"
    TRANSPOSITION = "transposition"
    REVERSAL = "reversal"
    SELF_CORRECTION = "self-correction"


STAGE3_LABELS: frozenset[WordLabel]  # correct, substitution, mispronunciation, omission, insertion
ARBITRATION_LABELS = STAGE3_LABELS  # arbitration never produces the post-pass labels


class PhoneticCheck(StrEnum):
    DONE = "done"
    SKIPPED = "skipped"
    UNVERIFIABLE = "unverifiable"


class AlignOp(StrEnum):
    MATCH = "match"
    SUB = "sub"
    DELETE = "delete"
    INSERT = "insert"
```

### Stage 0 output (Lane B)

- `PassageToken(index: int ≥ 0, raw: str, key: str)`: `raw` is the passage text as written; `key` is the normalised form.
- `PreparedPassage(passage_hash: str, tokens: tuple[PassageToken, ...], pronunciations: dict[str, tuple[PhoneSeq, ...]])`.
  - Every token key has at least one non-empty variant.
  - `tokens[i].index == i`.
  - Use a `Mapping` field converted to a read-only view, or validate and store it as a tuple of pairs; either way it must not be mutable from outside.

### Lane A output (owned by Lane A, interim definition here)

- `AsrWord(index, text, start_ms: int | None, end_ms: int | None, score: float in [0, 1])`.
- `TraceStep(op: AlignOp, passage_index: int | None, asr_index: int | None)`. `match`/`sub` have both indices, `delete` only `passage_index`, `insert` only `asr_index`.
- `Unit`: one aligned position.
  - `surface: str`: the ASR text; `""` for an omission.
  - `key: str`
  - `passage_index: int | None`: the first passage token covered; `None` for an insertion. *Not in the Rev 2 spec; added so Lane B can look up the expected phonemes.*
  - `passage_span: int`: ≥1 normally; 0 for an insertion.
  - `source_indices: tuple[int, ...]`: ASR word indices; empty for an omission.
  - `start_ms: int | None`, `end_ms: int | None`
  - `align_score: float`
  - `verifiable: bool`: a computed property, true when both times are present.
- `LaneAResult(units, labels, trace, asr_words)`:
  - `len(labels) == len(units)`.
  - Every label is in `STAGE3_LABELS`.
  - Units are in passage order, with insertions placed where they occur.

### Lane B intermediate

- `PhonemeToken(symbol: str, start_ms, end_ms, confidence: float in [0, 1])`.
- `CoverageCandidate(start_ms, end_ms, phonemes: tuple[PhonemeToken, ...], after_passage_index: int | None)`: `None` means before the first passage word.

### Arbitration output (input to the post-pass)

- `WordVerdict`:
  - `unit_index`
  - `expected_phonemes: tuple[PhoneSeq, ...]`
  - `best_variant_index: int | None`
  - `heard_phonemes: PhoneSeq`
  - `distance_expected: float | None`, `distance_hypothesis: float | None`
  - `deviations: tuple[tuple[str, str], ...]`: `("", x)` is an insertion, `(x, "")` a deletion
  - `stage3_label`, `label`
  - `phonetic_check`
- `ArbitrationResult(verdicts, coverage_candidates, ms_per_phoneme: tuple[float | None, ...], phonetic_check: PhoneticCheck)`:
  - `len(verdicts) == len(ms_per_phoneme)` and equals the unit count.
  - `verdicts[i].unit_index == i`.

### Validation rules (all models)

- Times are ints ≥ 0, and `end_ms ≥ start_ms` when both are set. A model with only one of the two set is invalid.
- Floats are finite (no NaN or Infinity, matching the worker contract). Distances are ≥ 0; scores and confidences are in [0, 1].
- `best_variant_index` is within the range of `expected_phonemes`.
- `phonetic_check != DONE` ⇒ `label == stage3_label` (the degraded path keeps Stage 3 labels).
- Phoneme symbols are non-empty and contain no whitespace.

## Fixture

`tests/fixtures/lane_a/synthetic_cat.json`: a hand-written `LaneAResult` plus a matching `PreparedPassage` for a 7-word sentence (for example, passage "The cat sat on the big mat"). It has at least one unit for each case:
- `correct`
- `substitution` (cat → cap)
- `mispronunciation` (a non-word)
- `omission` (no timing, empty surface)
- `insertion` (`passage_index` None, span 0)
- one correct unit with no timing (`verifiable == False`)

It also has a complete, consistent trace and ASR word list. Real-recording fixtures come later, when passage texts exist (see the questions below).

`conftest.py` provides `load_fixture(name) -> (PreparedPassage, LaneAResult)`.

## Tests

- **Round-trip:** for every model, `Model.model_validate_json(m.model_dump_json()) == m`.
- **Validators:** one failing case for each rule above, with `pytest.raises(ValidationError)`.
- **The synthetic fixture:** it loads, covers every `STAGE3_LABELS` member, and `verifiable` is right for each unit.
- **Immutability:** assigning to a field raises.
- **`test_contract_labels.py`:** load `../contracts/be-mai-openapi.yaml` with pyyaml and assert that `{l.value for l in WordLabel} - {"correct"}` equals `components.schemas.MiscueType.enum`. This catches contract drift the same way `server/tests/test_contract.py` does.

## Done when

- From `audio/`: `uv sync --extra dev`, `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .` all pass.
- `git status` does not show `audio/data/`.
- The ROADMAP marks B0 done, with these decisions recorded: pydantic frozen models; `Unit.passage_index` added; src layout; Python <3.13; single `WordLabel` enum.
- The root README "Current state" says the `audio/` scaffold and shared types exist.

## Decisions (approved with this plan)

1. **Pydantic v2 frozen models, not dataclasses.** They give free JSON round-trips for fixtures and validation at the stage boundaries, and they match the server's style. The cost is small (a few hundred objects per request).
2. **One `WordLabel` enum** whose values equal the contract strings, plus `correct`. Stage subsets are frozensets, not separate enums, so there is no conversion between stages.
3. **`Unit.passage_index` is added to the spec's `Unit`.** Flag it to the Lane A developer during reconciliation.
4. **src layout `src/audio/`,** following `projectstructure.txt` (the server's `app/` layout differs, but each project is independent).

## Open questions for the user (do not block B0)

- **Passage texts for the 6 passages.** B1 golden tests and the real-recording fixtures need the exact passage text, ideally from the BE/Supabase `passages` table. Recommendation: store them as `audio/tests/fixtures/passages/<passage_id>.txt` (passage text is not sensitive) plus a `audio/data/manifest.csv` (git-ignored) mapping each recording to its passage.
- **Labels for the 9 recordings.** Are there teacher word-level miscue labels? B6 needs them. If there are none, plan a labelling step: a CSV of `recording, word_index, passage_word, label` per recording.
- **Which recordings are from the target users** (Grade-level Filipino readers, Cebu cohort)? This matters for the accent table in B4 and for held-out splits in B6.
