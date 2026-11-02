# Phase B1.1 plan — Real passages and lexicon coverage

Status: draft, waiting for user approval. Run it **after B2 is committed**: it edits the fixtures and data files B2 is still using. Planning happens in one session and implementation in another. The implementing session follows this file and [../ROADMAP.md](../ROADMAP.md) "How to work a phase" steps 4–5. It does not change decisions here; if something does not work, it stops and reports.

## Goal

Replace the made-up placeholder passages with the real passage texts. Make Stage 0 handle every word in them correctly: Filipino and Spanish names, possessives of those names, and abbreviations. Add a fixed mapping from each recording to its passage.

## Source

The user exported the passages from Supabase. The export has one column (`text`), 8 rows and no titles. The recordings do not include the titles, which confirms B1 decision 4 (the title is not part of the passage).

Exact texts, extracted with no edits, are committed in [`docs/lane-b-context/passages/`](../../docs/lane-b-context/passages/): `ring_ring.txt`, `mama_was.txt`, `anansi.txt`, `every_saturday.txt`, `nick_is_a_tri.txt`, `a_mouse_and_a.txt`, `marian_came.txt`. Copy them from there. Row → file mapping in the original export:

| Row | Opening | File | Recordings (`audio/data/` prefix) |
|---|---|---|---|
| 1 | "Ring! Ring! rang the clock" | `ring_ring.txt` | `ringringra` (1) |
| 2 | "Mama was feeling sick" | `mama_was.txt` | `mamawas` (1) |
| 3 | "Anansi was tired of her web" | `anansi.txt` | none |
| 4 | "Every Saturday, Manuel goes to market" | `every_saturday.txt` | `everysatur` (3) |
| 5 | "Nick is a tricycle man" | `nick_is_a_tri.txt` | `nickisatri` (1) |
| 6 | "A mouse and a cat lived" | `a_mouse_and_a.txt` | `amouseanda` (1) |
| 7 | "Marian came home from school" | `marian_came.txt` | `mariancame` (2) |
| 8 | duplicate of row 4 except for one comma | skipped | |

Notes on the rows:
- Row 8 differs from row 4 only by the comma in "Today, Mang Ador". Punctuation never reaches keys or phonemes, so the choice does not matter; use row 4.
- Row 3 has no recording. Keep it anyway, as extra Stage 0 coverage.
- Copy the text exactly, byte for byte (UTF-8, curly quotes kept). Passage text is not sensitive and is committed.

## Changes

### 1. Fixtures

- Replace the six placeholder files in `audio/tests/fixtures/passages/` with the real texts and add `anansi.txt`.
- Rewrite `tests/fixtures/README.md`: these are the real passage texts, the source is the Supabase export, and the date is 2026-11-02.
- Tests that relied on placeholder-only features (a digit, a hyphen, a dash, a contraction such as `don't`) must not lose that coverage. Move those cases into inline strings in `test_passage.py` instead of deleting them. The real texts have curly apostrophes and quotes only: no digits, hyphens or dashes.

### 2. Recording → passage mapping

Add `audio/devtools/recordings.py` with `PASSAGE_BY_PREFIX: dict[str, str]`, built from the table above, and `passage_for(wav_path) -> str`, which matches the file name by prefix. Use the longest prefix, and raise if there is no match.
- This is used by the spike now, and by B3/B6 later.
- File names only, no audio, so it is committed.

### 3. Possessives and contractions in lexicon lookup

**Gap found:** `spoken_form("Juaning’s")` is `juaning's`, which is not in the lexicon, so espeak anglicises the whole word.

Change the pronunciation lookup in `passage.py`. When `spoken_form` ends in `'s` and the form without it is in the lexicon, use each lexicon pronunciation plus a final `z`, and also plus a final `s`. Both are accepted, so this adds no new decision for B4.

Add tests for `Juaning’s`, `Tinoy’s` and `Tita’s`. Non-lexicon possessives (for example `Nick's`) keep going to espeak unchanged.

`you’re` and other contractions stay with espeak; check that the golden test or a unit test covers `you’re`.

### 4. Lexicon entries

Add these to `lexicon.yaml`, listing the Filipino form first, following the B1 rules (five vowels, tap `ɾ`, no stress). Keep the `PLACEHOLDER` header, since nobody has reviewed these yet.

```yaml
ador: ["a d o ɾ", "ə d ɔ ɹ"]
taho: ["t a h o", "t ɑ h oʊ"]
mongo: ["m o ŋ ɡ o", "m ɑ ŋ ɡ oʊ"]
tita: ["t i t a", "t i t ə"]
tinoy: ["t i n o j", "t i n ɔɪ"]
juaning: ["h w a n i ŋ", "w ɑ n ɪ ŋ"]
manuel: ["m a n w ɛ l", "m æ n j u ɛ l"]
perez: ["p ɛ ɾ ɛ s", "p ɛ ɹ ɛ z"]
pardo: ["p a ɾ d o", "p ɑ ɹ d oʊ"]
anansi: ["a n a n s i", "ə n ɑ n s i"]
```

`mang` and `aling` already exist; check that they are there. Leave English names (Jacky, Kris, Lisa, Mike, Nick, Marian) to espeak. Add a golden check that each one gives a non-empty result containing no unknown symbols.

### 5. Abbreviations and interjections

`spoken_form("Mr.")` is `mr`, which espeak may spell letter by letter. Add these to `lexicon.yaml` under a `# abbreviations and interjections` comment, since the lexicon replaces espeak:

```yaml
mr: ["m ɪ s t ɚ"]
mrs: ["m ɪ s ɪ z", "m ɪ s ɪ s"]
mmm: ["m"]
```

Add a test that `Mr.` and `Mrs.` in a passage give these pronunciations.

### 6. Coverage check across the real passages (espeak)

Add one `espeak`-marked test. It prepares all seven real passages, then asserts that every key has at least one variant and that no segment is outside `INVENTORY`.

It also lists every word that goes to espeak (not the lexicon) and that starts with a capital letter in the middle of a sentence, compared against an allow-list of English names in the test (`jacky, kris, lisa, mike, nick, marian, mama, mom, saturday, cheese, i`). The test fails on any name not in the lexicon or the allow-list. This is the guard against a future passage with a Filipino name being silently anglicised.

### 7. Spike re-run (report only)

If B2's spike summary exists, add a second reference to `devtools/phoneme_spike.py`: `--reference passage`, which is G2P of the mapped passage text, first variant.
- Re-run the chosen model only.
- Append a "Passage reference" table to `plans/phase-B2-spike.md` with median PER per passage and the five recordings with the highest PER.
- This does not change the model choice; the B2 criteria stand. A high passage PER on one recording against a low Whisper-reference PER on the same recording points to reading miscues, which is the signal B4 and B5 are meant to catch.

## Done when

- All tests pass with nothing skipped on the dev machine. Both ruff checks pass.
- The ROADMAP has a B1.1 row marked done and records the decisions below. The root README "Current state" is unchanged unless something user-facing changed.
- One Conventional Commit on `feature/lane-b`, for example `feat(audio): use real passage texts and extend Filipino lexicon (B1.1)`.
  - No Claude or co-author trailers.
  - Nothing from `audio/data/`, `audio/devtools/out/` or `.env` is staged.
  - No push.

## Decisions (approved with this plan)

1. Use the real passage texts as fixtures, byte for byte. Row 4 is the canonical "Every Saturday" text. Anansi is kept as a fixture without a recording.
2. A lexicon word's possessive accepts both a final `z` and a final `s`.
3. Abbreviations and interjections go in the lexicon, because the lexicon replaces espeak.
4. A test fails when a capitalised mid-sentence word is in neither the lexicon nor an English-name allow-list.
