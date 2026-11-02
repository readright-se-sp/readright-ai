# Open scoring questions

Scoring decisions in the audio service that haven't been settled yet. Each entry describes what the code does today, why, and the options, so the team can decide without reading the code first.

---

## 1. A swap where both words are also misread

**Status:** open. The code currently uses option A (the conservative choice).

### The case

| | |
|---|---|
| Passage | she **was happy** to pet |
| Child said | she **hapy wos** to pet |

The child put the two words in the wrong order **and** misread both of them.

### What the service does today

2 substitutions = **2 errors**:

| Passage word | Child said | Tag | Counted |
|---|---|---|---|
| was | hapy | substitution | ✓ |
| happy | wos | substitution | ✓ |

It does **not** call this a swap. Each word is paired with the word in its own spot, so "hapy" is judged against "was". The two look nothing alike, so the tag is *substitution*, not *mispronunciation*.

### Why

The aligner only accepts a swap if **at least one** of the two crossed words is read exactly ([needleman_wunsch.py](src/audio/alignment/needleman_wunsch.py), `_fill_swap` and `_fill_crossed_reread`). There are two reasons:

1. **Weak evidence.** When neither word is right, a swap is only one possible explanation. Two plain misreadings explain it just as well.
2. **No room in the response.** Every passage word has exactly one row. A swap is 2 rows, and one misread half is already counted on its own row: "happy wos" = mispronunciation row + swap row = 2 errors. If both halves are misread, there are **3 miscues** (swap + 2 mispronunciations) but only 2 rows to put them in.

### By comparison, what is settled

| Child said | Result | Errors |
|---|---|---|
| she happy was | swap | 1 |
| she happy **wos** (one half misread) | swap + mispronunciation | 2 |
| she **hapy wos** (both misread) | 2 substitutions (this question) | 2 |

### Options

| | Result for "hapy wos" | Errors | What changes |
|---|---|---|---|
| **A. Keep as is** (current) | 2 substitutions | 2 | Nothing. The labels are misleading ("hapy" is shown as a misreading of "was"). |
| **B. Per-row error count** | swap + 2 mispronunciations | 3 | Add an `errors` field to each row (usually 1, here 2 on one row). `error_count` becomes the sum of `errors`, not the number of counted rows. **Response contract change**: anything that reads the response needs an update. |
| **C. Extra swap row** | swap + 2 mispronunciations | 3 | Add a third row for the swap with no passage word. **Contract change**: a swap is no longer always 2 rows. |
| **D. Swap, label-only** | swap rows tagged mispronunciation | 2 | Treat it as a swap, but keep 2 errors by not counting the transposition. Inconsistent with "happy wos" = 2. |

**Recommendation: A**, unless the Phil-IRI answer key requires 3.
- Both words misread *and* swapped is rare.
- A transposition is hard to prove when neither word was said correctly.
- If 3 is required, **B** is the cleaner contract change. It also handles any future case where one word carries two miscues.

### How to change it later

- **To allow both-misread swaps:** remove `or (a and b)` / `or (first and last)` from the crossing checks in `_fill_swap` and `_fill_crossed_reread`.
- **Then, to count the third miscue:**
  - for B, add the per-row count in `models.py` (`AlignedWord`) and `tagging/scoring.py`
  - for C, emit the extra row in `alignment/detection.py` (`_swap_rows`)
- **Either way:** bump `CONTRACT_VERSION` in `models.py`, and update `contracts/audio-service.md` and the swap tests in `tests/test_tagging.py`.
