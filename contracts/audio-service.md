# Audio service contract

**Version:** `1` (the `version` field of every response)

What the server sends the audio service, what comes back, and what every tag means. The audio service's own models (`audio/src/audio/models.py`) follow this file. Change this file first, then the code (see "Rules for changing a contract" in the README).

## Request

> **Not final.** The server and audio teams still need to agree on how the audio reaches the service. The proposal below assumes the shared volume from `compose.yaml`.

| Field | Type | Notes |
|---|---|---|
| `wav_path` | string | Path on the shared volume. 16 kHz mono WAV, already extracted by the server. |
| `passage_text` | string | The passage exactly as shown to the student. Punctuation and case are fine; the service normalizes them. |
| `proper_nouns` | string[] | Optional. Names in the passage (`"Mang Ador"`, `"Aling Tita"`). Any reading of these words counts as a match. |

An empty `passage_text` is an error.

## Response

```json
{
  "version": "3",
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
| `start`, `end` | number \| null | Seconds from the start of the recording. `null` when nothing was heard. |
| `tag` | string | See [Tags](#tags). |
| `move_id` | int \| null | Links the rows of one `swap` or `moved`. Numbered from 1 within a response. |
| `counted` | bool | `true` if this row is one Phil-IRI error. A swap or move is several rows but counts once, so only its first row is `true`. |
| `passage_indices` | int[] | Which passage words (0-based) the row covers. Every passage word appears in exactly one row. Empty for spoken-only rows. |
| `attempts` | string[] | Earlier tries at the word, before the final one (`["pork"]` for "pork park"). |
| `detail` | string \| null | Why a tag was given: `ending`, `miscorrection` or `proper_noun`. |

**`error_count`** is the number of rows with `counted: true`.
**`word_recognition_pct`** = `(total_words − error_count) / total_words × 100`, floored at 0.
**`reading_level`**: 97 and above is `Independent`, 91 and above is `Instructional`, anything lower is `Frustration`. A score exactly on a cut-off counts as the higher level.
**`wpm`** = `total_words / (last word end − first word start) × 60`, over the reading only: `off_text` rows (talk before or after the reading) are left out. It is `null` if the recording has no timing.

## Tags

### Base tags: set by the alignment

These are the tags listed in the README. They come from the Needleman–Wunsch alignment of the passage against the transcript.

| Tag | Counted | Row shape | Example | Code |
|---|---|---|---|---|
| `match` | no | both words | "dog" → dog | `alignment/detection.py`, `labels.py` (`base`) |
| `substitution` | yes | both words | "girl" → "boy" | `alignment/detection.py`, `labels.py` (`base`) |
| `omission` | yes | `spoken_word`, `start`, `end` null | "girl" skipped | `alignment/detection.py` |
| `insertion` | once per run | `passage_word` null, one row per word. Contiguous insertions are one error: only the first row of a run has `counted: true` ("I think this good" after the passage = 1). Fillers and false starts between them don't break the run. | "the **big** dog" | `alignment/detection.py`; run counting in `tagging/post_pass.py` |
| `swap` | once per `move_id` | 2 rows, one per passage word, each with the time that word was heard. The two words may have up to 20 words between them ("the cat sat on the mat" → "the mat sat on the cat"); those keep their own rows between the halves. With 2 or more words between, both crossed words must be exact, and with more than 6 words between, one of them needs 4+ letters. One half may be misread when the words are at most one apart ("happy wos"). That half is its own counted miscue: its row is tagged `mispronunciation` (or `substitution`/`reversal`) and keeps the swap's `move_id`, while the other half carries the swap. That's 2 errors in total. If both halves are misread, it isn't a swap (open question: `audio/OPEN_QUESTIONS.md` #1). A swap read twice ("happy was, happy wos") is a `repetition` plus the swap; the last reading is judged, so here "wos" is a mispronunciation with `detail: "miscorrection"`, 3 errors in total. A copy said just before the swap ("happy wos, happy was") adds a `repetition` row for the words said the same and puts changed words in the swap row's `attempts`. | "was happy" → "happy was" | `alignment/needleman_wunsch.py` (transposition states), `swaps.py`, `detection.py` |
| `repetition` | yes | `passage_word` null, one row per re-read. Only a contiguous repeat counts, meaning the word or phrase is said again straight away (fillers and false starts in between are ignored). With other words in between ("I… and I will" for "I will"), the earlier copy and the added words are insertions instead. Repeated several times, or restarted over the same words, is still one row (`"they can they can"`). The last reading of each word is the one judged; an earlier, different reading goes in that word's `attempts`. | "to to pet" | `alignment/needleman_wunsch.py` (re-read state), `repeats.py` |
| `moved` | once per `move_id` | Passage half: `passage_word` set, untimed. Heard half: `spoken_word` set, timed. A phrase gives one row per word on each side. It may travel up to 20 words. A move is only made if it doesn't add errors ("the cot sat on the cut" for "the cat sat on the cot" stays 2 mispronunciations). Every move needs an exact anchor word, and a move of more than 6 words needs a 4+ letter anchor or 2 exact words. Other words in a moved phrase may be misread: close (`word_distance` < 0.5) up to 6 words away, very close (< 0.25) beyond that. A misread word's heard half is its own counted miscue: it's tagged `mispronunciation` (or `substitution`/`reversal`/`letter_transposition`) with `passage_word` set, empty `passage_indices`, and the move's `move_id`. | "his father Mang Ador" → "Mang Ador father" | `alignment/moves.py` (`MoveRule`, `MoveAwareAligner`), `tagging/moved.py` |

### Derived tags: set after alignment

These are applied in `tagging/post_pass.py`, in the order below. When Lane B (phonemes and arbiter) exists, it runs **before** this step, so it sees only the base tags.

| Tag | Counted | Replaces | Rule | Code |
|---|---|---|---|---|
| `self_correction` | no | `match` with `attempts` | A word read more than once. The alignment finds the tries: one-word retries ("pork park", "pork perk park", "saw was"), re-read stretches ("ben rans ben runs") and swaps read twice. Each word row arrives with its earlier readings in `attempts`, and this step judges only the last reading. Right means self-correction. Wrong, with the right word said earlier, keeps the tag and gets `detail: "miscorrection"`, which is counted. A swap half keeps its `swap` tag, so a correction there shows only in `attempts`. | `alignment/needleman_wunsch.py` (re-read and retry states), `repeats.py`, `swaps.py`; verdict in `tagging/attempts.py` (`AttemptJudge`) |
| `mispronunciation` | yes | `substitution` | The spelling is close (`coast` → "cost") or only the ending differs (`tomatoes` → "tomato", `detail: "ending"`). When unsure, mispronunciation is chosen over substitution. | `tagging/mispronunciation.py` |
| `reversal` | yes | `mispronunciation` / `substitution` | The word read backwards, flipped or mirrored (`was` → "saw", `pot` → "top", `on` → "no"). | `tagging/reversal.py`, `alignment/text.py` (`is_reversal`) |
| `letter_transposition` | yes | `mispronunciation` / `substitution` | Letters out of order but not a full flip (`from` → "form", `animal` → "aminal"), possibly with another slip (`girls` → "gril"). One miscue per word. Phil-IRI transposition = `swap` + `moved` + `letter_transposition`. | `tagging/reversal.py`, `alignment/text.py` (`is_letter_transposition`) |

### Noise: recorded, never counted

| Tag | Rule | Code |
|---|---|---|
| `filler` | A bracketed CrisperWhisper tag (`[UM]`, `[LAUGH]`) or a word like um, uh, er, hmm. A word that's in the passage is never a filler. | `alignment/cleanup.py` |
| `false_start` | A cut-off word (`Th-`), or the start of the next word when it isn't in the passage ("ox oxygen"). | `alignment/cleanup.py` |
| `off_text` | Talk before or after the reading. **After:** extra words after the last passage word the child read, when the ASR ended the sentence on that word ("…I will. **I think this good.**"). **Before:** extra words before the first passage word read, when the ASR ended a sentence just before it ("**Okay, I'll read.** A mouse and a cat…"). Words that run into the reading are still insertions ("…possible **at all**.", "**The** a mouse…"). The decision uses punctuation, not timing. | `alignment/off_text.py` |

## Normalization

Both the passage and the transcript are normalized before alignment, so these never cause a miscue on their own:

- case, punctuation and quotes;
- apostrophes (`Juaning's` → `juanings`);
- hyphens and slashes, which split words;
- digits, which are spelled out (`3` → `three`);
- a passage word split by the ASR (`pan de sal` → `pandesal`), which is rejoined;
- adjacent passage words joined by the ASR (`Mangador` → `mang ador`), which are split back, with the time divided by letter count.

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
