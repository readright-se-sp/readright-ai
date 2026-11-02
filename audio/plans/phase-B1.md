# Phase B1 plan — Stage 0 passage preparation

Status: approved by the user on 2026-11-02; ready to implement. Open questions are deferred by the user. Planning happens in one session and implementation in another. The implementing session follows this file and [../ROADMAP.md](../ROADMAP.md) "How to work a phase" steps 4–5. It does not change decisions here; if something does not work, it stops and reports.

## Goal

`PassagePreparer.prepare(content) -> PreparedPassage`. It tokenises the passage, assigns each token a matching key, and gives every key one or more accepted IPA pronunciations. The pronunciations come from espeak-ng, a variant table and a hand-written lexicon, and the result is cached by passage hash. B1 includes no recogniser and no comparison.

## Before you start

- All Lane B work goes on one branch, `feature/lane-b`, with no per-phase branches. B0 is committed on the local branch `feature/lane-b-project-setup` (`01cf948`, not pushed). Rename it with `git branch -m feature/lane-b-project-setup feature/lane-b`. If `feature/lane-b` already exists, check it out instead.
- **Install espeak-ng; it is a system dependency.** It is not installed on the dev machine yet.
  - Windows: install the `.msi` from the espeak-ng GitHub releases, then set `AUDIO_ESPEAK_LIBRARY=C:\Program Files\eSpeak NG\libespeak-ng.dll` in `audio/.env`. Document this in `audio/README.md`.
  - Linux/Docker (B7): `apt-get install espeak-ng`.

## Files

| File | Action |
|---|---|
| `audio/pyproject.toml` | add `phonemizer>=3.3` and `pyyaml` to the main dependencies (pyyaml moves out of dev); register a pytest marker `espeak` |
| `audio/src/audio/settings.py` | add the settings below |
| `audio/.env.example` | add the settings below |
| `audio/src/audio/phonetics/__init__.py` | new |
| `audio/src/audio/phonetics/ipa.py` | new: inventory and normalisation |
| `audio/src/audio/phonetics/g2p.py` | new: `G2P` protocol, `EspeakG2P` |
| `audio/src/audio/phonetics/lexicon.py` | new: loads the YAML data files |
| `audio/src/audio/phonetics/data/lexicon.yaml` | new |
| `audio/src/audio/phonetics/data/variants.yaml` | new |
| `audio/src/audio/passage.py` | new: tokeniser, interim key normaliser, `PassagePreparer` |
| `audio/src/audio/types.py` | tighten the `LaneAResult` passage-order check (see "B0 follow-up") |
| `audio/tests/fixtures/passages/*.txt` | new: 6 placeholder passages |
| `audio/tests/fixtures/README.md` | describe the passages and mark them as placeholders |
| `audio/tests/conftest.py` | add `FakeG2P`, `load_passage(name)`, and auto-skip for the `espeak` marker |
| `audio/tests/test_ipa.py`, `test_lexicon.py`, `test_g2p.py`, `test_passage.py` | new |
| `audio/README.md`, `audio/ROADMAP.md`, root `README.md` | update when done |

Package data: make sure hatch includes `phonetics/data/*.yaml` in the wheel. Load the files with `importlib.resources`.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `AUDIO_ESPEAK_LANGUAGE` | `en-us` | espeak voice |
| `AUDIO_ESPEAK_LIBRARY` | unset | path to `libespeak-ng.dll`/`.so`; when set, export it as `PHONEMIZER_ESPEAK_LIBRARY` before the backend is created |
| `AUDIO_PASSAGE_CACHE_SIZE` | `128` | number of prepared passages kept in memory |

## `phonetics/ipa.py`

Every phoneme sequence anywhere in the lane (espeak output, lexicon, variants, and later the recogniser output) passes through `normalise_segments(segments: Iterable[str]) -> PhoneSeq`. Steps, in order, for each segment:

1. Unicode NFD.
2. Remove the stress marks `ˈ ˌ`, the syllable mark `.`, tie bars `͡ ͜`, the linking mark `‿`, and the length marks `ː ˑ`.
3. Apply `SYMBOL_MAP`: `ᵻ→ɪ`, `ɐ→ə`, `g→ɡ` (ASCII g to IPA ɡ), `r→ɹ`, `ɝ→ɜ`, `ʔ→` (drop). This is a declared dict, so B2 can extend it.
4. NFC again. Drop segments that became empty.

- `INVENTORY: frozenset[str]` holds the allowed segments. That is every segment the en-us espeak voice produces after normalisation, plus the Tagalog-specific `ɾ`. It includes the multi-character units `tʃ dʒ eɪ aɪ ɔɪ aʊ oʊ`, the rhotic vowels `ɚ ɜ`, and the syllabic consonants `n̩ l̩` if espeak emits them.
  - Build it by running all six placeholder passages, every lexicon and variant entry, and a few hundred common words through the normaliser.
  - Freeze the resulting set in code and review it by hand.
- Any segment not in `INVENTORY` is kept but logs a warning, at most once per symbol per process. Expose `unknown_symbols(seq) -> set[str]` for tests.

**Decision: length marks are stripped.** The en-us tense/lax contrast survives in the vowel quality (`i`/`ɪ`, `u`/`ʊ`), and the B4 accent table already treats those pairs as near-equal. Keeping `ː` would make the recogniser's output and espeak's output disagree for no gain.

## `phonetics/g2p.py`

```python
class G2P(Protocol):
    def phonemize(
        self, words: Sequence[str]
    ) -> list[PhoneSeq]: ...  # same length and order as words
```

- `EspeakG2P(language)` creates one `phonemizer.backend.EspeakBackend(language, with_stress=False, preserve_punctuation=False, language_switch="remove-flags", words_mismatch="ignore")` and reuses it. Creating the backend is the expensive step.
- Call `backend.phonemize(words, separator=Separator(phone=" ", word="|", syllable=""), strip=True, njobs=1)`, one input line per word. Split each output on `|` and then on spaces, so a word that espeak expands into several words (for example "5" → "five", "Mr." → "mister") is joined into one sequence. Then call `normalise_segments`.
- An empty result for a word raises `G2PError(word)`.
- No caching here; caching lives in `PassagePreparer`.

## Data files

Both files map a **lowercase spoken form** (see `spoken_form` below) to a list of pronunciations. Each pronunciation is one string of **space-separated** IPA segments, already in normalised form.

`variants.yaml` holds pronunciations added to espeak's own, for function words and heteronyms (the variant table has ~20 entries). The full starting set:

```yaml
the: ["ð ə", "ð i"]
a: ["ə", "eɪ"]
an: ["ə n", "æ n"]
and: ["ə n d", "æ n d", "ə n"]
to: ["t ə", "t u"]
of: ["ə v", "ʌ v"]
for: ["f ɚ", "f ɔ ɹ"]
was: ["w ə z", "w ʌ z"]
from: ["f ɹ ə m", "f ɹ ʌ m"]
at: ["ə t", "æ t"]
can: ["k ə n", "k æ n"]
you: ["j u", "j ə"]
read: ["ɹ i d", "ɹ ɛ d"]
live: ["l ɪ v", "l aɪ v"]
lead: ["l i d", "l ɛ d"]
wind: ["w ɪ n d", "w aɪ n d"]
tear: ["t ɪ ɹ", "t ɛ ɹ"]
close: ["k l oʊ s", "k l oʊ z"]
wound: ["w u n d", "w aʊ n d"]
bow: ["b oʊ", "b aʊ"]
```

`lexicon.yaml` holds Filipino proper nouns and loanwords. An entry **replaces** espeak for that word. Each entry lists the Filipino pronunciation first and the anglicised one second, because children may read either.
- Write 30–40 entries. Cover every Filipino word in the placeholder passages, plus common ones: aling, nena, mang, lola, lolo, nanay, tatay, ate, kuya, cebu, manila, luzon, mindanao, jeepney, pandesal, adobo, sinigang, halo-halo, barangay, sari-sari, tricycle, bahay, kubo, maria, juan, jose, rizal, mabuhay, salamat, peso.
- Filipino form: a five-vowel system `a ɛ i o u` (written `o` and `ɛ` as they are), tap `ɾ` for `r`, `ŋ` for `ng`, no aspiration, and stress dropped. Example: `aling: ["a l i ŋ", "æ l ɪ ŋ"]`, `cebu: ["s ɛ b u", "s eɪ b u"]`, `jeepney: ["dʒ i p n i"]`, `pandesal: ["p a n d ɛ s a l", "p æ n d ə s æ l"]`.
- Add a comment at the top: `# PLACEHOLDER: pronunciations not yet reviewed by a Filipino speaker.`

`lexicon.py` loads both files once (functools cache) into `dict[str, tuple[PhoneSeq, ...]]`, calling `normalise_segments` on each pronunciation. Keys must already be lowercase; reject duplicate keys in a file and empty lists.

## `passage.py`

### Tokenising

- `tokenise(content) -> list[str]`: NFC, then split on whitespace. Drop tokens that contain no letter or digit (`—`, `-`, `...`). Each surviving token is a `raw` token.
- This matches Phil-IRI counting: the scoring denominator is the number of raw tokens (B7).
- The passage **title is not part of the passage**. `prepare` takes only the content.

### Two forms per token

- `spoken_form(raw)` is the G2P input and the lexicon/variant lookup key. It is lowercase, with leading and trailing punctuation stripped. Keep internal apostrophes and hyphens (`don't`, `ice-cream`, `nick's`). Typographic apostrophes `’` become `'`.
- `interim_key(raw)` is the matching key: `spoken_form` with apostrophes and hyphens removed (`dont`, `icecream`, `nicks`).
  - It is the interim stand-in for Lane A's Normalise (Rev 2 rules 1, 4 and 7 only).
  - Put it behind one function, so reconciliation replaces only that function.
  - Note in its docstring that number words, contractions, possessives and homophones (rules 2, 3, 5, 6, 8) are Lane A's job.

### Pronunciations for one spoken form

1. If it is in the lexicon, use the lexicon entries only.
2. Otherwise, use espeak's pronunciation first, followed by the `variants.yaml` entries.
3. Remove duplicates while keeping order.

Several spoken forms can map to the same key (for example `nick's` and `nicks`). The key's pronunciation list is the ordered, de-duplicated union over all its spoken forms in the passage.

### `PassagePreparer`

- `PassagePreparer(g2p: G2P, cache_size: int)`
- `prepare(content: str) -> PreparedPassage`
- `passage_hash = sha256(NFC(content) with whitespace runs collapsed to one space and stripped).hexdigest()`. The token list comes from the same normalised text.
- Call G2P **once per passage**, on the unique spoken forms that are not in the lexicon, in a single batch.
- The cache is an `OrderedDict` LRU keyed by `passage_hash`, guarded by a `threading.Lock`. On a hit, return the stored object, which is immutable.
- If G2P raises `G2PError`, let `PassagePreparationError(token)` propagate. Do not cache it. Mapping it to a worker error code is B7's job (recommendation there: 400 `INVALID_JOB`).

## B0 follow-up (types.py)

Tighten the `LaneAResult` order check. For units with a `passage_index`, require `passage_index >= previous.passage_index + previous.passage_span`, so no two units can claim the same passage token. Add one failing test for overlapping units. Make sure `synthetic_cat.json` still loads.

## Placeholder passages

Write 6 files in `tests/fixtures/passages/`, named after the recordings' file-name prefixes. Each opening phrase continues the prefix:

| File | Must start with |
|---|---|
| `a_mouse_and_a.txt` | "A mouse and a" |
| `every_saturday.txt` | "Every Saturday" |
| `mama_was.txt` | "Mama was" |
| `marian_came.txt` | "Marian came" |
| `nick_is_a_tri.txt` | "Nick is a tri" (for example "Nick is a tricycle driver") |
| `ring_ring.txt` | "Ring, ring!" |

- Each passage is 60–120 words at Grade 1–3 level, in plain text with no header.
- Across the six, include at least once:
  - a digit ("5 pesos")
  - two contractions (don't, it's)
  - a possessive (Nick's)
  - a hyphenated compound (halo-halo or sari-sari)
  - a heteronym in context (read or live)
  - a dash token (`—`)
  - curly apostrophes in one passage
  - Filipino names and words from the lexicon (Aling Nena, Cebu, jeepney, pandesal)
- `tests/fixtures/README.md` states that these are **made-up placeholders**, not the texts the recordings read. Real texts replace them before any real-recording fixture or calibration (B6). Tests must not depend on their exact wording beyond the opening phrase.

## Tests

Tests marked `espeak` need espeak-ng. `conftest.py` skips them with a clear reason when the backend cannot be created. CI without espeak still passes; the dev machine runs them all.

**`test_ipa.py`** (no espeak)
- Stress, length, tie bars and syllable marks are stripped (`ˈtʃeɪ.ɹː` → `tʃ eɪ ɹ`).
- `SYMBOL_MAP` is applied, and empty segments are dropped.
- An unknown symbol is kept and reported by `unknown_symbols`.

**`test_lexicon.py`** (no espeak)
- Both files load, all keys are lowercase, and there are no empty lists.
- **Every segment of every entry is in `INVENTORY`.** This catches typos.

**`test_g2p.py`** (`espeak`)
- Hard assertions:
  - cat `k æ t`
  - fish `f ɪ ʃ`
  - ship `ʃ ɪ p`
  - thin `θ ɪ n`
  - this `ð ɪ s`
  - jump `dʒ ʌ m p`
  - sing `s ɪ ŋ`
  - day `d eɪ` (diphthong as **one** segment)
  - chair starts with `tʃ`
- Output length equals input length, and "5" gives a non-empty sequence.
- Golden file `tests/fixtures/g2p_golden.yaml` covers ~60 common Grade 1–3 words.
  - Generate it once, then review it by hand: fix the generator if output looks wrong, never edit expected values to match.
  - Use it as a regression test against espeak version drift.
  - Record the espeak-ng version in the file header.

**`test_passage.py`** (mostly `FakeG2P`)
- Tokenising: punctuation-only tokens are dropped, and `tokens[i].index == i`.
- `spoken_form` and `interim_key` on `Don't`, `Nick's,`, `ice-cream.`, `“Hello!”` and `it’s`.
- A lexicon word bypasses G2P (`FakeG2P` records calls), and variants are appended after the espeak entry and de-duplicated.
- Two spoken forms with the same key give the union of their pronunciations.
- G2P is called once per passage with the unique non-lexicon forms. The second `prepare` of the same content (with different whitespace) is a cache hit with no G2P call. LRU eviction works at `cache_size=2`.
- `G2PError` becomes `PassagePreparationError`, and nothing is cached.
- `espeak`: all six placeholder passages prepare. Every key has at least one variant, and **no segment is outside `INVENTORY`**. A warm `prepare` of one passage takes under 1 s; log the timing.

## Done when

- From `audio/`: `uv sync --extra dev`, `uv run pytest` (with espeak-ng installed, nothing skipped), `uv run ruff check .` and `uv run ruff format --check .` all pass.
- `audio/README.md` documents installing espeak-ng and `AUDIO_ESPEAK_LIBRARY`.
- The ROADMAP marks B1 done and records the decisions below. The root README "Current state" is updated.
- **Commit:** on `feature/lane-b`, one Conventional Commit, for example `feat(audio): add stage 0 passage preparation with espeak-ng IPA (B1)`.
  - No Claude or co-author trailers.
  - Check that `audio/data/` and `.env` are not staged.
  - Do not push or open a PR unless the user asks.

## Decisions (approved with this plan)

1. espeak-ng via `phonemizer`, voice `en-us`, with no stress marks and length marks stripped.
2. The lexicon replaces espeak; variants add to it. The lexicon lists the Filipino form first and the anglicised form second.
3. Heteronyms get both readings (no part-of-speech disambiguation in v1).
4. The passage title is not read and not scored.
5. G2P input (`spoken_form`) and matching key (`interim_key`) are separate. The key normaliser is an interim stand-in for Lane A's Normalise, kept to one function.
6. The lexicon lives in code-side YAML, not in `passages.proper_nouns` (that column does not exist yet).

## Open questions (do not block B1)

- **Real passage texts.** They replace the placeholders. Every Filipino word in them must be added to the lexicon (the test that checks segments against `INVENTORY` does not catch a missing lexicon word; espeak will anglicise it silently).
- **Lexicon review by a Filipino speaker.** The entries are placeholders until reviewed. Recommendation: review them before B6 calibration.
- **Does the student read the title aloud?** If they do, CrisperWhisper will produce extra words at the start, which Lane A aligns as insertions. Recommendation: confirm with the BE/FE how recording starts. If titles are read, strip a leading title match in Lane A, not in Stage 0.
