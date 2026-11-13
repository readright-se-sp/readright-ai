# Audio worker image: CrisperWhisper (CTranslate2) + Lane B phoneme model, baked in at build time.
# Build context is audio/. Needs the NVIDIA container runtime to run (docker compose, GPU).
#
#   docker build -t readright-mai-audio audio/
#
# Python is 3.12, the newest pyproject.toml allows (requires-python < 3.13).

ARG CUDA_IMAGE=nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04

# ---- base: system packages and the locked Python environment (no project code) ----
FROM ${CUDA_IMAGE} AS base

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates espeak-ng ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=3.12 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    PATH="/srv/.venv/bin:$PATH" \
    PYTHONPATH=/srv/src \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- models: download and convert (needs network; the final image never does) ----
FROM base AS models

COPY models.lock ./
COPY devtools/fetch_models.py ./devtools/fetch_models.py
RUN python -m devtools.fetch_models --model-dir /models

# ---- final ----
FROM base AS final

RUN useradd --create-home --uid 1000 mai

COPY --from=models /models /models
COPY src ./src

ENV HF_HOME=/models/hf \
    TORCH_HOME=/models/torch \
    HF_HUB_OFFLINE=1 \
    AUDIO_ASR_MODEL=/models/crisper2_large_ct2 \
    AUDIO_PHONEME_MODEL=/models/phoneme \
    AUDIO_WORKER_ALLOWED_MEDIA_DIR=/scratch

USER mai
EXPOSE 8001

HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health/ready', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "audio.worker.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8001"]
