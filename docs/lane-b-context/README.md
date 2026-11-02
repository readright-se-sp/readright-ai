# Lane B planning context (temporary)

Remove this folder when Lane B implementation (audio/ROADMAP.md phases B0 to B7) is done.

It carries the context that planning and implementation sessions need on any machine. It is not a source of truth: decisions in `audio/ROADMAP.md`, `audio/plans/` and `contracts/` take precedence.

## Files

- `ReadRight_GO2_AI_Architecture_Specification_Rev2.docx`: GO2 pipeline spec (Rev 2, 31 Oct 2026). Lanes, stages 0 to 6, arbitration rules, accent table, miscue taxonomy, parameters.
- `go2_arch_rev2.txt`: plain-text extraction of the docx, tables included.
- `readright_technical_design_ai.pdf`: handwritten pipeline sketch (Lane A, Lane B, join by time, arbitrate, post-pass) and miscue examples. No text layer.
- `projectstructure.txt`: early repo layout. Predates Rev 2; `audio/ROADMAP.md` has the current layout.
- `passages/`: the real passage texts (Supabase export, 2026-11-02), byte for byte. Used by Phase B1.1.

Where the spec says WhisperX, read CrisperWhisper (agreed design).

## Working agreements

- Planning and implementation run in separate sessions. Planning writes `audio/plans/phase-<id>.md`; the implementing session follows it exactly and stops to report instead of improvising. Side tasks (checks, extraction, shell work) go to cheaper models.
- All Lane B work is committed on `feature/lane-b`. No per-phase branches.
- Commits use Conventional Commits with no Claude or co-author trailers. Push only when the user asks.
- Ownership in `audio/`: Lane B (Stage 0 passage prep, phoneme recogniser, join, compare, arbitration) is this branch's owner. Another developer owns Lane A (CrisperWhisper, normalise, NW align, detect) and the Stage 5 post-pass. Shared types, worker API and scoring are interim on the Lane B side and get reconciled when Lane A's code lands.
- Phonemes are IPA (espeak-ng conventions, normalised by `audio/src/audio/phonetics/ipa.py`). An omission's span is the gap where the word should have been read.
- Passage titles are not read aloud in the recordings and are not scored.

## Machine setup

- Recordings are not in git. Copy them into `audio/data/` (git-ignored) from the team's Google Drive folder; ask the user for the link.
- Install espeak-ng (Windows `.msi` from the espeak-ng GitHub releases). On Windows the default install path is detected; otherwise set `AUDIO_ESPEAK_LIBRARY`.
- Optional: set `HF_HOME` and `UV_CACHE_DIR` to a large disk. Model weights are about 1.3 GB per model.

## Open questions (deferred by the user)

- Review of the Filipino lexicon by a Filipino speaker.
- Word-level teacher labels for the recordings (needed for B6 calibration).
- Which recordings come from the target users (Filipino Grade 1 to 3 readers).
