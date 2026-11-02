# Phase B2 spike: phoneme model and precision

Aggregate numbers only (no transcripts or student content). Raw per-recording rows are in
`devtools/out/results.csv` (git-ignored). Run on the dev GPU (GTX 1660 Ti, 6 GB); A10G
numbers come from the deployment benchmark.

Reference: `openai/whisper-small.en` word transcript, phonemised with `build_g2p` (first variant). PER
measures how well each recogniser follows what was said, not whether the reading was
correct. Recordings: 9 WAVs from `audio/data/`.

## Results (medians over recordings)

| Config | PER | Word-window coverage | Warm s | RTF | Peak mem (MB) | NaN/Inf |
|---|---|---|---|---|---|---|
| `wav2vec2-lv-60-espeak-cv-ft / cuda / fp32` | 0.358 | 1.000 | 0.86 | 0.019 | 1667 | - |
| `wav2vec2-lv-60-espeak-cv-ft / cuda / fp16` | 1.000 | 0.000 | 3.54 | 0.080 | 1222 | 8108128 |
| `wav2vec2-xlsr-53-espeak-cv-ft / cuda / fp32` | 0.368 | 1.000 | 0.86 | 0.019 | 1667 | - |
| `wav2vec2-xlsr-53-espeak-cv-ft / cuda / fp16` | 1.000 | 0.000 | 3.54 | 0.080 | 1222 | 8108128 |
| `wav2vec2-xlsr-53-espeak-cv-ft / cpu / fp32` | 0.368 | 1.000 | 12.18 | 0.266 | 2436 | - |

Peak memory is `torch.cuda.max_memory_allocated` on CUDA and process peak RSS on CPU.

## Longest recording

| Config | Audio s | Warm s |
|---|---|---|
| `wav2vec2-lv-60-espeak-cv-ft / cuda / fp32` | 67.2 | 1.3 |
| `wav2vec2-lv-60-espeak-cv-ft / cuda / fp16` | 67.2 | 5.48 |
| `wav2vec2-xlsr-53-espeak-cv-ft / cuda / fp32` | 67.2 | 1.292 |
| `wav2vec2-xlsr-53-espeak-cv-ft / cuda / fp16` | 67.2 | 5.482 |
| `wav2vec2-xlsr-53-espeak-cv-ft / cpu / fp32` | 67.2 | 18.526 |

## Phonemes outside the English inventory (counts over all recordings)

- `wav2vec2-lv-60-espeak-cv-ft / cuda / fp32`: `ʉ` x6, `e` x2, `x` x2
- `wav2vec2-xlsr-53-espeak-cv-ft / cuda / fp32`: `e` x16, `ø` x2, `y` x1, `ɨ` x1, `x` x1
- `wav2vec2-xlsr-53-espeak-cv-ft / cpu / fp32`: `e` x16, `ø` x2, `y` x1, `ɨ` x1, `x` x1

## Decision

- Model: `facebook/wav2vec2-xlsr-53-espeak-cv-ft` (medians within 0.02, so the lower warm GPU latency wins).
- Precision: `AUDIO_PHONEME_FP16=false`.

## Notes

- The two models are within noise of each other: PER differs by about 0.01 and warm
  GPU latency by about 1%. The pre-agreed rule (medians within 0.02, so the lower
  latency wins) picks the xlsr model; lv-60 has the slightly lower PER and fewer
  non-English phones.
- fp16 produced NaN/Inf logits on the GTX 1660 Ti (no tensor cores) for every
  recording, so its PER is 1.0 and it is unusable here. This says nothing about the
  A10G; re-check there in the deployment benchmark before enabling it.
- Word-window coverage is near 1.0 because Whisper word windows plus 80 ms padding
  are almost contiguous over the speech; it is an early read on B3's join, not a
  sharp measure.

## Passage reference (B1.1)

`facebook/wav2vec2-xlsr-53-espeak-cv-ft`, fp32, cuda. Reference: G2P of the mapped passage text, first
variant (`--reference passage`). The Whisper column is the B2 reference on the same
recordings. The B2 model choice is unchanged.

| Passage | Recordings | Median PER (passage ref) | Median PER (Whisper ref) |
|---|---|---|---|
| `a_mouse_and_a` | 1 | 0.212 | 0.213 |
| `every_saturday` | 3 | 0.343 | 0.421 |
| `mama_was` | 1 | 0.516 | 0.584 |
| `marian_came` | 2 | 0.371 | 0.409 |
| `nick_is_a_tri` | 1 | 0.337 | 0.238 |
| `ring_ring` | 1 | 0.475 | 0.368 |

Five highest PER against the passage:

| Recording | Passage | PER (passage ref) | PER (Whisper ref) |
|---|---|---|---|
| `mamawasfeef5fb` | `mama_was` | 0.516 | 0.584 |
| `ringringraf85a` | `ring_ring` | 0.475 | 0.368 |
| `mariancamea8fe` | `marian_came` | 0.426 | 0.528 |
| `everysatur88f3` | `every_saturday` | 0.411 | 0.421 |
| `everysaturbc0e` | `every_saturday` | 0.343 | 0.347 |
