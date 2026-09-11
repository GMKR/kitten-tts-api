# Kitten text-to-speech service.
#
# Build and run from this directory:
#   docker compose up -d
# The model is baked in at build time, so the container boots offline and
# answers /health within seconds.
#
# The service installs KittenTTS from a pinned commit on main, which is ahead
# of the last wheel release (0.8.1). Main drops the torch, spacy, and misaki
# dependencies and adds robust text normalization, streaming, and GPU backends.
#
# Available models, largest and highest quality first:
#   KittenML/kitten-tts-mini-0.8        80M  (default)
#   KittenML/kitten-tts-micro-0.8       40M
#   KittenML/kitten-tts-nano-0.8-fp32   15M
#   KittenML/kitten-tts-nano-0.8-int8   15M  (reported issues; avoid)
# Switch with:
#   docker compose build --build-arg KITTEN_MODEL=KittenML/kitten-tts-micro-0.8

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    PORT=8788

WORKDIR /app

ARG KITTEN_TTS_REF=be5758500b731b8fc674acc62ea480d3022b7ebe
RUN pip install --upgrade pip \
 && pip install "https://github.com/KittenML/KittenTTS/archive/${KITTEN_TTS_REF}.tar.gz" \
 && pip install fastapi "uvicorn[standard]" soxr

COPY server.py /app/server.py

ARG KITTEN_MODEL=KittenML/kitten-tts-mini-0.8
ENV KITTEN_MODEL=${KITTEN_MODEL}
RUN python -c "import os; from kittentts import KittenTTS; KittenTTS(os.environ['KITTEN_MODEL'])"

EXPOSE 8788
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8788/health')" || exit 1

CMD ["python", "server.py"]
