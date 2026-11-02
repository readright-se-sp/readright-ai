# Audio service contract

**Version:** `7` (the `version` field of every response)

What the server sends the audio service, what comes back, and what every tag means. The audio service's own models (`audio/src/audio/models.py`) follow this file. Change this file first, then the code (see "Rules for changing a contract" in the README).

## Request

> **Not final.** The server and audio teams still need to agree on how the audio reaches the service. The proposal below assumes the shared volume from `compose.yaml`.

| Field | Type | Notes |
|---|---|---|
| `wav_path` | string | Path on the shared volume. 16 kHz mono WAV, already extracted by the server. |
| `passage_text` | string | The passage exactly as shown to the student. Punctuation and case are fine; the service normalizes them. |
| `proper_nouns` | string[] | Optional. Names in the passage (`"Mang Ador"`, `"Aling Tita"`). If none are given, they're guessed from the passage: capitalised words that don't start a sentence or a quote. A name, including its possessive (`"Tinoy"` covers "Tinoy's"), only forgives a **same-sounding spelling** ("Jackie" for "Jacky"), because the ASR doesn't know names. A real misreading of a name counts like any other word (Phil-IRI has no name exception): "Jack" for "Jacky" is a mispronunciation, "Bob" a substitution. The aligner also pairs a heard word with a name more readily, so a misread name is one misreading, not an omission plus an insertion. |

An empty `passage_text` is an error.

## Response

```json
{
  "version": "7",
  "words": [ /* rows, in reading order (see below) */ ],
  "score": {
    "counts": { "match": 9, "mispronunciation": 1, "substitution": 0, "omission": 0, "insertion": 1,
                "repetition": 1, "swap": 1, "moved": 0, "reversal": 0, "letter_transposition": 0,
                "self_correction": 1, "filler": 1, "false_start": 0, "off_text": 0 },
    "total_words": 13,
    "error_count": 4,
    "word_recognition_pct": 69.2,
    "wpm": 92.9,
    "reading_level": "Frustration"
  },
  "transcript": [ { "text": "[UM]", "start": 0.0, "end": 0.4, "score": 0.9 } /* raw ASR words */ ]
}
```

### Rows (`words`)

The README defines the core of each row as `(passage_word, spoken_word, start, end, tag, move_id)`. The service adds four bookkeeping fields.

| Field | Type | Meaning |
|---|---|---|
| `passage_word` | string \| null | Normalized passage word (lowercase, no punctuation). `null` when the words were only spoken. |
| `spoken_word` | string \| null | Normalized heard word. `null` when the passage word was skipped. A repeated phrase is one row with its words joined by spaces (`"they can"`). |
| `start`, `end` | number \| null | Seconds from the start of the recording. For an `omission`, it's the span where the word should have been read (see Tags). `null` for the passage half of a moved word, or when the recording has no timing. |
| `tag` | string | See [Tags](#tags). |
| `move_id` | int \| null | Links the rows of one `swap` or `moved`. Numbered from 1 within a response. |
| `counted` | bool | `true` if this row is one Phil-IRI error. A swap or move is several rows but counts once, so only its first row is `true`. |
| `passage_indices` | int[] | Which passage words (0-based) the row covers. Every passage word appears in exactly one row. Empty for spoken-only rows. |
| `attempts` | string[] | Earlier tries at the word, before the final one (`["pork"]` for "pork park"). |
| `detail` | string \| null | Why a tag was given: `ending`, `miscorrection` or `proper_noun`. |
| `phonetic` | object | Optional (absent when Lane B did not judge the row). What Lane B heard: see [Phonetic evidence](#phonetic-evidence). |

**`error_count`** is the number of rows with `counted: true`.
**`word_recognition_pct`** = `(total_words − error_count) / total_words × 100`, floored at 0.
**`reading_level`**: 97 and above is `Independent`, 91 and above is `Instructional`, anything lower is `Frustration`. A score exactly on a cut-off counts as the higher level.
**`wpm`** = `total_words / (last word end − first word start) × 60`, over the reading only: `off_text` rows (talk before or after the reading) are left out. It is `null` if the recording has no timing.

### Phonetic evidence

Lane B (the phonetic lane) re-judges the rows tagged `match`, `substitution`, `mispronunciation`, `omission` or `insertion` from the sound of the recording. Rows with any other tag are not judged and have no `phonetic` field. The field is also absent on every row when Lane B is turned off (`AUDIO_PHONETIC_ENABLED=false`), fails, or runs out of time (logged as `phonetic_check=skipped`; the request still succeeds with Lane A's tags).

```json
"phonetic": { "expected_phonemes": ["k", "ɑ", "s", "t"], "heard_phonemes": ["k", "ɔ", "s", "t"],
              "d_exp": 0.12, "d_hyp": null, "check": "done" }
"phonetic": { "expected_phonemes": ["f", "ɪ", "ʃ"], "heard_phonemes": ["f"],
              "d_exp": 0.8, "d_hyp": null, "check": "unverifiable", "reason": "short_word" }
```

| Field | Type | Meaning |
|---|---|---|
| `expected_phonemes` | string[] | The accepted pronunciation of the passage word(s) that sounds closest to what was heard, as IPA segments (espeak-ng conventions, after `audio/src/audio/phonetics/ipa.py` normalisation: no stress marks or tie bars). Empty for an `insertion`. |
| `heard_phonemes` | string[] | The phonemes the recogniser placed inside this row's time window, same notation. Empty when none were heard. |
| `d_exp` | number \| null | Weighted phoneme distance from `heard_phonemes` to the passage word (0 identical; accent-table pairs cost almost nothing). `null` when not computed. |
| `d_hyp` | number \| null | The same distance to the word the ASR wrote, for `substitution` / `mispronunciation` rows. `null` when not computed. |
| `check` | string | `done`: Lane B decided the tag, so the letter-based `mispronunciation` rule does not run on this row. `unverifiable`: no timing, no phonemes heard, or a name the pronunciation lexicon does not cover; Lane A's tag stands. `skipped`: reserved. |
| `reason` | string | Optional (added in v6). Present when `check` is `unverifiable`: why Lane B left Lane A's tag alone. One of `untimed` (the row has no timing), `no_heard` (no phonemes in the window), `name_not_in_lexicon` (a name the pronunciation lexicon does not cover), `low_coverage` (a `match` row where Lane B heard too few of the word's phonemes to overrule it), `short_word` (a `match` row for a very short word, which Lane B never overrules), `match_kept` (added in v7: a `match` row that Lane B would have overruled, kept because `AUDIO_OVERRULE_MATCH` is off; `d_exp` and the phonemes are still given). New values may be added; treat an unknown one as "unverifiable". |

What Lane B may change on a row: `tag` among `match`, `substitution`, `mispronunciation`, `omission` and `insertion` (and `counted` with it). A `match` row is only overruled when Lane B heard the whole word (`AUDIO_COVERAGE_GUARD_RATIO`, default 1.0) and the word has more than `AUDIO_SHORT_WORD_MAX_PHONES` (default 3) phonemes; this keeps false overrules of correct reads under 2% on the calibration recordings (`audio/plans/phase-B6a.md`). A row Lane A tagged `mispronunciation` with detail `ending` may only end as `mispronunciation` or `match`, never `substitution`. Never the row order, the passage coverage, or any other field. A name (`proper_nouns`, or guessed) that is not in the lexicon is never turned from `match` into `mispronunciation` by sound: its `check` is `unverifiable`.

## Tags

### Base tags: set by the alignment

These are the tags listed in the README. They come from the Needleman–Wunsch alignment of the passage against the transcript.

| Tag | Counted | Row shape | Example | Code |
|---|---|---|---|---|
| `match` | no | both words | "dog" → dog | `alignment/detection.py`, `labels.py` (`base`) |
| `substitution` | yes | both words | "girl" → "boy" | `alignment/detection.py`, `labels.py` (`base`) |
| `omission` | yes | `spoken_word` null. `start`/`end` = where the word should have been read: the gap between the last heard word before it and the first heard word after it. Several omissions in a row share that gap by letter count. At the very start or end of the reading, the span is zero-length at the edge. | "girl" skipped | `alignment/detection.py` (`place_omissions`) |
| `insertion` | once per run | `passage_word` null, one row per word. Contiguous insertions are one error: only the first row of a run has `counted: true` ("I think this good" after the passage = 1). Fillers and false starts between them don't break the run. | "the **big** dog" | `alignment/detection.py`; run counting in `tagging/post_pass.py` |
| `swap` | once per `move_id` | 2 rows, one per passage word, each with the time that word was heard. The two words may have up to 20 words between them ("the cat sat on the mat" → "the mat sat on the cat"); those keep their own rows between the halves. With 2 or more words between, both crossed words must be exact, and with more than 6 words between, one of them needs 4+ letters. One half may be misread when the words are at most one apart ("happy wos"). That half is its own counted miscue: its row is tagged `mispronunciation` (or `substitution`/`reversal`) and keeps the swap's `move_id`, while the other half carries the swap. That's 2 errors in total. If both halves are misread, it isn't a swap (open question: `audio/OPEN_QUESTIONS.md` #1). A swap read twice ("happy was, happy wos") is a `repetition` plus the swap; the last reading is judged, so here "wos" is a mispronunciation with `detail: "miscorrection"`, 3 errors in total. A copy said just before the swap ("happy wos, happy was") adds a `repetition` row for the words said the same and puts changed words in the swap row's `attempts`. | "was happy" → "happy was" | `alignment/needleman_wunsch.py` (transposition states), `swaps.py`, `detection.py` |
| `repetition` | yes | `passage_word` null, one row per re-read. Only a contiguous repeat counts, meaning the word or phrase is said again straight away (fillers and false starts in between are ignored). With other words in between ("I… and I will" for "I will"), the earlier copy and the added words are insertions instead. An extra word that looks like the passage word right after the re-read is a first try at that word, not an added word ("I can't, I, I cannot": `cant` tried `cannot`), so the repeat still counts and that word gets the try in `attempts`. Repeated several times, or restarted over the same words, is still one row (`"they can they can"`). The last reading of each word is the one judged; an earlier, different reading goes in that word's `attempts`. | "to to pet" | `alignment/needleman_wunsch.py` (re-read state), `repeats.py` |
| `moved` | once per `move_id` | Passage half: `passage_word` set, untimed. Heard half: `spoken_word` set, timed. A phrase gives one row per word on each side. It may travel up to 20 words. A move is only made if it doesn't add errors ("the cot sat on the cut" for "the cat sat on the cot" stays 2 mispronunciations). Every move needs an exact anchor word, and a move of more than 6 words needs a 4+ letter anchor or 2 exact words. Other words in a moved phrase may be misread: close (`word_distance` < 0.5) up to 6 words away, very close (< 0.25) beyond that. A misread word's heard half is its own counted miscue: it's tagged `mispronunciation` (or `substitution`/`reversal`/`letter_transposition`) with `passage_word` set, empty `passage_indices`, and the move's `move_id`. | "his father Mang Ador" → "Mang Ador father" | `alignment/moves.py` (`MoveRule`, `MoveAwareAligner`), `tagging/moved.py` |

### Derived tags: set after alignment

These are applied in `tagging/post_pass.py`, in the order below. Lane B (phonemes and arbiter) runs **before** this step, so it sees only the base tags. After it, the `mispronunciation` rule below skips rows whose `phonetic.check` is `done`.

| Tag | Counted | Replaces | Rule | Code |
|---|---|---|---|---|
| `self_correction` | no | `match` with `attempts` | A word read more than once. The alignment finds the tries: one-word retries ("pork park", "pork perk park", "saw was"), re-read stretches ("ben rans ben runs") and swaps read twice. Each word row arrives with its earlier readings in `attempts`, and this step judges only the last reading. Right means self-correction. Wrong, with the right word said earlier, keeps the tag and gets `detail: "miscorrection"`, which is counted. A swap half keeps its `swap` tag, so a correction there shows only in `attempts`. | `alignment/needleman_wunsch.py` (re-read and retry states), `repeats.py`, `swaps.py`; verdict in `tagging/attempts.py` (`AttemptJudge`) |
| `mispronunciation` | yes | `substitution` | Rows Lane B judged by sound are skipped. The spelling is close (`coast` → "cost") or only the ending differs (`tomatoes` → "tomato", `detail: "ending"`). When unsure, mispronunciation is chosen over substitution. | `tagging/mispronunciation.py` |
| `reversal` | yes | `mispronunciation` / `substitution` | The word read backwards, flipped or mirrored (`was` → "saw", `pot` → "top", `on` → "no"). | `tagging/reversal.py`, `alignment/text.py` (`is_reversal`) |
| `letter_transposition` | yes | `mispronunciation` / `substitution` | Letters out of order but not a full flip (`from` → "form", `animal` → "aminal"), possibly with another slip (`girls` → "gril"). One miscue per word. Phil-IRI transposition = `swap` + `moved` + `letter_transposition`. | `tagging/reversal.py`, `alignment/text.py` (`is_letter_transposition`) |

### Noise: recorded, never counted

| Tag | Rule | Code |
|---|---|---|
| `filler` | A bracketed CrisperWhisper tag (`[UM]`, `[LAUGH]`) or a word like um, uh, er, hmm. A word that's in the passage is never a filler. When the passage has a hum ("Mmm, Cheese!"), any hum the child makes (um, `[UM]`, hmm, mmm) can be its reading and is matched to it; other hums stay fillers. | `alignment/cleanup.py` |
| `false_start` | A cut-off word (`Th-`), or the start of the next word when it isn't in the passage ("ox oxygen"). | `alignment/cleanup.py` |
| `off_text` | Talk before or after the reading. **After:** extra words after the last passage word the child read, when the ASR ended the sentence on that word ("…I will. **I think this good.**"). **Before:** extra words before the first passage word read, when the ASR ended a sentence just before it ("**Okay, I'll read.** A mouse and a cat…"). Words that run into the reading are still insertions ("…possible **at all**.", "**The** a mouse…"). The decision uses punctuation, not timing. | `alignment/off_text.py` |

## Normalization

Both the passage and the transcript are normalized before alignment, so these never cause a miscue on their own:

- case, punctuation and quotes;
- apostrophes (`Juaning's` → `juanings`);
- hyphens and slashes, which split words;
- digits, which are spelled out (`3` → `three`);
- a passage word split by the ASR (`pan de sal` → `pandesal`), which is rejoined;
- adjacent passage words joined by the ASR (`Mangador` → `mang ador`), which are split back, with the time divided by letter count. Near-misses are handled in the alignment, which knows where each word sits. **Split:** one heard word can read 2–3 neighbouring passage words (`Montenoy's` for "Mang Tinoy's"). It's cut where its letters line up with the word boundaries (`mon` + `tenoys`), so each passage word gets its own piece of audio. **Merge:** 2–3 heard pieces can read one passage word (`Jo ang` for "Juaning's", also compared without the possessive "s"). That gives one row, timed from the first piece to the last. Both happen only when the joined form is close and closer than any single word, and only for the words at that spot.

The service needs CrisperWhisper's **verbatim** ("as spoken") output. The cleaned-up ("as meant") text has no fillers, cut-offs or repeats, so those miscues would be lost.

## Sample

The passage is "The small girl was happy to pet the brown dog at the park.", read as "[UM] The small girl happy was to to pet the browns big dog at the pork park". The rows below are abridged: rows with only `match` are left out, as are the default bookkeeping fields.

```json
[
  { "passage_word": null,    "spoken_word": "um",     "start": 0.0, "end": 0.4, "tag": "filler",           "move_id": null, "counted": false },
  { "passage_word": "was",   "spoken_word": "was",    "start": 2.5, "end": 2.9, "tag": "swap",             "move_id": 1,    "counted": true  },
  { "passage_word": "happy", "spoken_word": "happy",  "start": 2.0, "end": 2.4, "tag": "swap",             "move_id": 1,    "counted": false },
  { "passage_word": null,    "spoken_word": "to",     "start": 3.0, "end": 3.4, "tag": "repetition",       "move_id": null, "counted": true  },
  { "passage_word": "brown", "spoken_word": "browns", "start": 5.0, "end": 5.4, "tag": "mispronunciation", "move_id": null, "counted": true, "detail": "ending" },
  { "passage_word": null,    "spoken_word": "big",    "start": 5.5, "end": 5.9, "tag": "insertion",        "move_id": null, "counted": true  },
  { "passage_word": "park",  "spoken_word": "park",   "start": 7.5, "end": 8.4, "tag": "self_correction",  "move_id": null, "counted": false, "attempts": ["pork"] }
]
```

A moved word: "with his father Mang Ador", read as "with see Mang Ador father".

```json
[
  { "passage_word": "with",   "spoken_word": "with",   "start": 0.0, "end": 0.4, "tag": "match",        "move_id": null, "counted": false },
  { "passage_word": "his",    "spoken_word": "see",    "start": 0.5, "end": 0.9, "tag": "substitution", "move_id": null, "counted": true  },
  { "passage_word": "father", "spoken_word": null,     "start": null, "end": null, "tag": "moved",      "move_id": 1,    "counted": true  },
  { "passage_word": "mang",   "spoken_word": "mang",   "start": 1.0, "end": 1.4, "tag": "match",        "move_id": null, "counted": false },
  { "passage_word": "ador",   "spoken_word": "ador",   "start": 1.5, "end": 1.9, "tag": "match",        "move_id": null, "counted": false },
  { "passage_word": null,     "spoken_word": "father", "start": 2.0, "end": 2.4, "tag": "moved",        "move_id": 1,    "counted": false }
]
```

## Open decisions

1. **The request format** above is a proposal. The server team needs to confirm it.
2. **Sound reversals** (`bad` → "dub", `his` → "see") use different letters, so the service tags them as substitutions. Lane B's phonemes are needed to call them reversals.
3. **A word with its letters reordered** (`Manuel` → "nuelMa") is tagged `reversal`. One of the team's answer keys treats it as a transposition (`swap`).
4. **Teacher prompts** are transcribed as the student's reading.
5. **A phrase skipped** counts as one omission per word. **A phrase moved** counts as one error.
6. **Prosody flags** (`is_monotonous`, `lack_expression`, `is_disregard_punctuation`, `is_word_by_word`) are part of the audio results in the SE plan but aren't in this response yet.
