# Phase B2 plan — Phoneme recogniser

Status: approved by the user on 2026-11-02; ready to implement. Planning happens in one session and implementation in another. The implementing session follows this file and [../ROADMAP.md](../ROADMAP.md) "How to work a phase" steps 4–5. It does not change decisions here; if something does not work, it stops and reports.

## Goal

`PhonemeRecognizer.recognise(wav_path, deadline_ms) -> tuple[PhonemeToken, ...]`: one pass over the whole recording, giving IPA segments in the same normalised form as Stage 0, with start and end times on a 20 ms grid. The phase also includes a spike that picks the model and precision from measured numbers.

All work is committed on `feature/lane-b`.

## Environment (measured 2026-11-02)

- Dev GPU: GTX 1660 Ti, 6 GB, CUDA 12.3 driver. It has no tensor cores; fp16 runs but must be checked for accuracy.
- 12 CPU threads, 32 GB RAM, Python 3.11, no ffmpeg on PATH.
- Production is an A10G 24 GB (deployment doc). The production timings come from the deployment benchmark, not this phase.
- The input WAV is always 16 kHz, 16-bit, mono, because the server converts it. B2 reads it with the stdlib `wave` module. There is no ffmpeg or torchaudio dependency (spec §8 notes Windows fragility in both).

## Part 1 — B1 follow-ups (do first)

### 1a. Split merged segments in `ipa.py`

B1 kept espeak's merged units (`ɛɹ ɑɹ ɔɹ ɪɹ ʊɹ əl aɪə aɪɚ iə`) as single segments. That breaks two things:
- `variants.yaml` writes `for` as `f ɔ ɹ`, while espeak produces `f ɔɹ`. They are the same pronunciation in two segmentations, so de-duplication fails, and B4 would count a false difference.
- The recogniser's vocabulary will not match these units one for one.

Change:
- Add `SPLIT_MAP` to `ipa.py`: `ɑɹ→ɑ ɹ`, `ɔɹ→ɔ ɹ`, `ɛɹ→ɛ ɹ`, `ɪɹ→ɪ ɹ`, `ʊɹ→ʊ ɹ`, `əl→ə l`, `aɪə→aɪ ə`, `aɪɚ→aɪ ɚ`, `iə→i ə`. It runs as step 5 of `normalise_segments`, after `SYMBOL_MAP`.
- Add a fallback, step 6: a segment that is not in `INVENTORY` but can be split greedily (longest match first) into `INVENTORY` segments is split. A segment that cannot be split is kept and logged, as before.
- Remove the merged units from `INVENTORY`. Keep `n̩` (button), the diphthongs `eɪ aɪ ɔɪ aʊ oʊ`, and the affricates `tʃ dʒ`.
- Regenerate `g2p_golden.yaml`. The normaliser changed on purpose, so this is the one allowed regeneration. Review the diff by hand: only the merged units should change. Update the test for `chair` (`tʃ ɛ ɹ`).
- Add tests: `normalise_segments(["f", "ɔɹ"]) == ("f", "ɔ", "ɹ")`; the greedy fallback splits `"oɹ"`-style leftovers; `for` from espeak and from `variants.yaml` de-duplicate to one variant.

### 1b. espeak library path

21 tests were skipped on the dev machine because `AUDIO_ESPEAK_LIBRARY` was unset (there is no `audio/.env`).
- Add `build_g2p(settings) -> EspeakG2P` in `g2p.py`. It is the only place a `G2P` is built from settings, and it passes `settings.espeak_library` through.
- On Windows only, when the setting is unset and `C:\Program Files\eSpeak NG\libespeak-ng.dll` exists, use that path and log it at INFO.
- Add tests: the setting is forwarded (monkeypatch the backend); the Windows default applies only when the setting is unset.
- With this in place, the dev machine needs no `.env` for espeak. Do not create or edit any `.env` file.

## Part 2 — Recogniser

### Dependencies

Add to the main dependencies: `torch>=2.6`, `transformers>=4.40,<5`, `numpy`. Amendment (2026-11-02): transformers refuses to `torch.load` the candidates' `pytorch_model.bin` on torch < 2.6 (CVE-2025-32434), and torch 2.6+ has no cu121 wheels, so the index is cu126.

Install torch from the CUDA 12.6 wheel index on Windows and Linux. The wheels bundle their own CUDA runtime and run on any 12.x driver through CUDA minor-version compatibility (Windows driver ≥ 528.33; the dev machine has 546.01). The A10G image uses a 12.x driver as well:

```toml
[[tool.uv.index]]
name = "pytorch-cu126"
url = "https://download.pytorch.org/whl/cu126"
explicit = true

[tool.uv.sources]
torch = { index = "pytorch-cu126" }
```

Register two new pytest markers. Both skip with a clear reason when the requirement is missing:
- `model`: needs the model weights in the Hugging Face cache, or network access to download them
- `gpu`: needs CUDA

Model weights download to the default Hugging Face cache (`HF_HOME`). Baking them into the Docker image is B7.

### Files

| File | Action |
|---|---|
| `audio/src/audio/errors.py` | new: `MediaUnreadable`, `DeadlineExceeded`, `RecognizerError` (the worker error codes are mapped in B7) |
| `audio/src/audio/recognition/__init__.py` | new |
| `audio/src/audio/recognition/audio_io.py` | new: `load_wav(path) -> np.ndarray` |
| `audio/src/audio/recognition/ctc.py` | new: pure numpy (chunk plan, stitch, collapse) |
| `audio/src/audio/recognition/vocab.py` | new: model token → normalised segments |
| `audio/src/audio/recognition/phoneme_recognizer.py` | new: Protocol + `Wav2Vec2PhonemeRecognizer` |
| `audio/src/audio/settings.py`, `audio/.env.example` | new settings |
| `audio/devtools/phoneme_spike.py` | new: the spike script |
| `.gitignore` | add `audio/devtools/out/` |
| `audio/tests/test_audio_io.py`, `test_ctc.py`, `test_vocab.py`, `test_recognizer.py` | new |

### Settings

| Setting | Default | Meaning |
|---|---|---|
| `AUDIO_PHONEME_MODEL` | `facebook/wav2vec2-lv-60-espeak-cv-ft` | Hugging Face id or local path; the default is updated after the spike |
| `AUDIO_PHONEME_DEVICE` | `auto` | `auto` (CUDA if available), `cuda`, `cpu` |
| `AUDIO_PHONEME_FP16` | `false` | set after the spike; ignored on CPU |
| `AUDIO_PHONEME_CHUNK_S` | `20` | audio per forward pass |
| `AUDIO_PHONEME_CONTEXT_S` | `2` | extra audio on each side of a chunk; its frames are discarded |

### `audio_io.py`

`load_wav(path) -> np.ndarray[float32]` with values in [−1, 1).
- Raise `MediaUnreadable` if the file is missing or not a WAV, or is not 16 kHz / 16-bit / mono, or has zero frames.
- Never resample. A wrong format means the server is broken, so fail loudly.

### `ctc.py` (no torch import; all tests run without a model)

- `plan_chunks(n_samples, sr, chunk_s, context_s) -> list[Chunk]`. Each chunk has an input span (`core` widened by `context_s` each side, clipped to the audio) and a core span. Core spans cover the whole audio, do not overlap, and fall on 20 ms frame boundaries (320 samples).
- `stitch(chunk_logprobs, chunks) -> np.ndarray[T, V]`: keep only each chunk's core frames and concatenate them. The result has `ceil(n_samples / 320)` frames, with an off-by-one tolerance that is documented and tested.
- `collapse(logprobs, blank_id) -> list[Span(token_id, start_frame, end_frame, confidence)]`:
  - greedy argmax per frame
  - a run of the same non-blank id is one span
  - a blank between two equal ids starts a new span
  - `confidence` is the mean of `exp(logprob)` of the chosen id over the span
- Times: `start_ms = start_frame * 20`, `end_ms = (end_frame + 1) * 20`. Clip `end_ms` to the audio length.

### `vocab.py`

`build_token_map(vocab: dict[str, int]) -> dict[int, PhoneSeq]`:
- Special tokens (`<pad>`, `<s>`, `</s>`, `<unk>`, `|`, and anything else the tokenizer lists as special) map to `()` and are dropped from the output.
- Every other token is run through `normalise_segments([token])`. A token can map to 0, 1 or several segments (for example `ɛɹ` → `ɛ ɹ`, `iː` → `i`).
- A span whose token maps to several segments is split into that many tokens. Each one gets an equal share of the span's frames, at least one frame each, all with the span's confidence.
- `unmapped_tokens(map)` lists tokens that give segments outside `INVENTORY`. These are non-English phones from the multilingual vocabulary. They are kept, not dropped: B4 scores them through feature distance. The spike reports which ones actually occur.

### `phoneme_recognizer.py`

```python
class PhonemeRecognizer(Protocol):
    def recognise(self, wav_path: Path, deadline_ms: int | None) -> tuple[PhonemeToken, ...]: ...
    @property
    def ready(self) -> bool: ...
```

`Wav2Vec2PhonemeRecognizer(model_id, device, fp16, chunk_s, context_s)`:
- **Load once, in the constructor:** `AutoProcessor` + `AutoModelForCTC`, `eval()`, moved to the device, `.half()` when fp16 is on CUDA. Build the token map. Run one warm-up pass on 1 s of silence. `ready` becomes true.
- **Input normalisation** uses the processor's feature extractor (its `do_normalize` setting). Apply it per chunk input, matching how the model was trained on short clips.
- **`recognise`:**
  1. `load_wav`.
  2. Check the deadline. If it has passed, raise `DeadlineExceeded`.
  3. `plan_chunks`.
  4. For each chunk: check the deadline, then run a forward pass under `torch.inference_mode()`. Compute `log_softmax` in fp32 and move it to the CPU.
  5. `stitch` → `collapse` → token map → `PhonemeToken`s, sorted by `start_ms`.
- **Errors:** any torch or CUDA error becomes `RecognizerError`, with the original chained. B5 uses this for graceful degradation.
- **Thread safety:** one `threading.Lock` around inference. Batching across requests is B7's concern, not this phase's.
- **No speech:** if the recording has no speech, the result is `()`. Mapping that to `NO_SPEECH` is B7, combined with ASR.

### Tests

- **`test_audio_io.py`:** write small WAVs with `wave` into `tmp_path`. A valid one loads with the right length and range. Stereo, 8 kHz, 24-bit, empty and missing files each raise `MediaUnreadable`.
- **`test_ctc.py`:**
  - `plan_chunks` core spans cover the audio exactly, for lengths of 0.5 s, 20 s, 20.01 s and 67 s.
  - `stitch` frame count is correct, and the frames at chunk boundaries come from the right chunk (use synthetic logprobs encoding the frame index).
  - `collapse` on hand-made sequences: repeats, blank-separated duplicates, all blank, last frame; also check confidences and times.
- **`test_vocab.py`:** a fake vocab with `<pad>`, `|`, `ɛɹ`, `iː`, `tʃ` and a non-English vowel gives the expected map. A span split into several segments shares frames correctly.
- **`test_recognizer.py`:**
  - Unit: with a fake model object returning fixed logits, `recognise` gives the expected tokens, raises `DeadlineExceeded` for a past deadline before running inference, and wraps a model exception in `RecognizerError`.
  - Integration (`model` + `espeak`): synthesise "the cat sat on the mat" with the `espeak-ng` CLI into `tmp_path`. espeak writes 22.05 kHz, so resample to 16 kHz with `numpy.interp`; this is test-only. Recognise it and assert:
    - PER ≤ 0.35 against `EspeakG2P` of the sentence (plain Levenshtein over the concatenated segments)
    - times are increasing and within the audio length
  - No student audio goes in the repo.

## Part 3 — Spike (`devtools/phoneme_spike.py`)

**Candidates:**
1. `facebook/wav2vec2-lv-60-espeak-cv-ft`
2. `facebook/wav2vec2-xlsr-53-espeak-cv-ft`

Each is about 300M parameters and licensed Apache-2.0.

**Recordings:** all 9 WAVs in `audio/data/`.

**Reference:** the real passage texts are not available yet, so the reference is a Whisper transcript.
- Run `openai/whisper-small.en` through the `transformers` ASR pipeline, chunked, with `return_timestamps="word"`.
- Phonemise the words with `build_g2p`, taking the first variant.
- This measures how well each recogniser follows what was said. It does not judge correctness.
- Run Whisper first, then free it (`del` + `torch.cuda.empty_cache()`), so only one model is on the 6 GB GPU at a time.
- Cache transcripts in `audio/devtools/out/whisper/*.json`.

**Measure for each candidate × {fp32, fp16} on CUDA, plus fp32 on CPU for the best candidate:**
- PER per recording against the reference (Levenshtein over the whole sequence), and the median.
- Word-window coverage: the share of recognised phonemes whose midpoint falls inside some reference word window `[start−80, end+80]` ms. This gives an early read on B3's join.
- Warm wall-clock per recording (median of 2 runs after a warm-up), and the real-time factor.
- Peak memory: `torch.cuda.max_memory_allocated` on GPU, process RSS on CPU.
- Tokens seen that `unmapped_tokens` reports, with counts.
- fp16 only: any NaN or Inf in the logits, and the PER difference from fp32.

**Output:**
- `audio/devtools/out/` (git-ignored) holds the raw CSV per recording, plus one aligned dump for the first `everysatur` recording: reference word, the recogniser's phonemes in its window, and times. The user can listen and compare.
- `audio/plans/phase-B2-spike.md` (committed) holds the summary tables. It has no transcripts or other student content beyond aggregate numbers.

**Choosing the model (fixed in advance, so the implementer can decide):**
- **Model:** lowest median PER. If the two medians are within 0.02 of each other, choose the lower warm GPU latency.
- **Precision:** set `AUDIO_PHONEME_FP16=true` only if the chosen model in fp16 has no NaN/Inf on any recording and its median PER is within 0.01 of fp32. Otherwise use fp32.
- Write the choice into the settings defaults, `.env.example`, the B2 section of the ROADMAP, and the spike summary.

**If the numbers are unusable, stop and report** instead of choosing. That means:
- median PER above 0.6 for both models, or
- a 67 s recording taking more than 15 s warm on the GPU.

## Done when

- Part 1 is done, with the golden diff reviewed.
- All tests pass from `audio/` on the dev machine with nothing skipped except `gpu` tests on CPU-only machines. Both ruff checks pass.
- The spike summary is committed, and the model and precision are chosen and recorded.
- `audio/README.md` documents the CUDA torch install, the model cache location, the first-run download (about 1.3 GB per candidate), and how to run the spike.
- The ROADMAP marks B2 done with its decisions recorded, and the root README "Current state" is updated.
- One Conventional Commit on `feature/lane-b`, for example `feat(audio): add wav2vec2 phoneme recogniser and model spike (B2)`.
  - No Claude or co-author trailers.
  - `audio/data/`, `audio/devtools/out/` and `.env` are not staged.
  - No push.

## Decisions (approved with this plan)

1. Merged espeak units are split into single segments; `INVENTORY` holds only single phones plus diphthongs, affricates and `n̩`.
2. The recogniser is wav2vec2 CTC through `transformers`, with greedy decoding and times on the 20 ms frame grid. No beam search and no language model; they would bias the output toward real words, which hides the errors we are looking for.
3. Audio is processed in 20 s chunks with 2 s context on each side, and the context frames are discarded.
4. The WAV is read with stdlib `wave`; no ffmpeg or torchaudio.
5. Non-English tokens from the multilingual vocabulary are kept, not dropped.
6. Because the passage texts are not available, the spike reference is a Whisper transcript, and the selection criteria are fixed in advance.

## Open questions (do not block B2)

- **The A10G numbers** (real latency with CrisperWhisper on the same GPU) come from the deployment benchmark. If the recogniser plus CrisperWhisper do not fit the 75 s analysis window, the options in order are: fp16, a smaller chunk count per batch, or running the recogniser on CPU while the GPU runs ASR.
- **Fine-tuning** on Filipino child speech is out of scope for v1. Revisit it after B6 if PER on real recordings is poor.
