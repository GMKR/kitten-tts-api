"""HTTP service for the Kitten text-to-speech model.

Loads the model once at boot, then serves synthesis requests. Every knob the
KittenTTS library exposes is available per request, with an environment
variable supplying the default.

Endpoints:
    GET  /health            Service and model status.
    GET  /voices            Voice names, raw identifiers, and defaults.
    POST /tts               Synthesize speech, buffered.
    POST /tts/stream        Synthesize speech, streamed chunk by chunk.
    POST /normalize         Preview text normalization without synthesis.
    POST /v1/audio/speech   OpenAI-compatible synthesis.
    GET  /docs              Interactive OpenAPI documentation.

The model always generates at 24 kHz. A request that asks for another sample
rate is resampled with soxr, so the pitch and duration stay correct.
"""

from __future__ import annotations

import io
import os
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterator, Literal, Optional

import numpy as np
import soundfile as sf
import soxr
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

MODEL_SAMPLE_RATE = 24000
UNKNOWN_SIZE = b"\xff\xff\xff\xff"

# Container, libsndfile subtype, and media type for each output format.
FORMATS: dict[str, tuple[Optional[str], Optional[str], str]] = {
    "wav": ("WAV", "PCM_16", "audio/wav"),
    "flac": ("FLAC", None, "audio/flac"),
    "ogg": ("OGG", "VORBIS", "audio/ogg"),
    "opus": ("OGG", "OPUS", "audio/ogg"),
    "mp3": ("MP3", None, "audio/mpeg"),
    "pcm": (None, None, "audio/L16"),
}
FormatName = Literal["wav", "flac", "ogg", "opus", "mp3", "pcm"]

# OpenAI voice names mapped onto the closest Kitten voice, so a client written
# against the OpenAI speech API works without changes.
OPENAI_VOICES = {
    "alloy": "Bruno",
    "ash": "Jasper",
    "ballad": "Hugo",
    "coral": "Rosie",
    "echo": "Hugo",
    "fable": "Jasper",
    "nova": "Luna",
    "onyx": "Leo",
    "sage": "Kiki",
    "shimmer": "Bella",
    "verse": "Leo",
}

# Every TextPreprocessor flag, with the value KittenTTS itself uses. Punctuation
# is kept because it carries the prosody.
PREPROCESSOR_DEFAULTS: dict[str, bool] = {
    "lowercase": True,
    "replace_numbers": True,
    "replace_floats": True,
    "expand_contractions": True,
    "expand_model_names": True,
    "expand_ordinals": True,
    "expand_percentages": True,
    "expand_currency": True,
    "expand_time": True,
    "expand_ranges": True,
    "expand_units": True,
    "expand_scale_suffixes": True,
    "expand_scientific_notation": True,
    "expand_fractions": True,
    "expand_decades": True,
    "expand_phone_numbers": True,
    "expand_ip_addresses": True,
    "normalize_leading_decimals": True,
    "expand_roman_numerals": False,
    "remove_urls": True,
    "remove_emails": True,
    "remove_html": True,
    "remove_hashtags": False,
    "remove_mentions": False,
    "remove_punctuation": False,
    "remove_stopwords": False,
    "normalize_unicode": True,
    "remove_accents": False,
    "remove_extra_whitespace": True,
}


def env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


class Defaults:
    """Server-wide defaults, each one overridable per request."""

    model = env_str("KITTEN_MODEL", "KittenML/kitten-tts-mini-0.8")
    backend = env_str("KITTEN_BACKEND", "") or None
    cache_dir = env_str("KITTEN_CACHE_DIR", "") or None
    voice = env_str("KITTEN_VOICE", "Bruno")
    speed = env_float("KITTEN_SPEED", 1.0)
    output_format = env_str("KITTEN_FORMAT", "wav")
    sample_rate = env_int("KITTEN_SAMPLE_RATE", MODEL_SAMPLE_RATE)
    gain_db = env_float("KITTEN_GAIN_DB", 0.0)
    clean_text = env_bool("KITTEN_CLEAN_TEXT", True)
    max_chunk_chars = env_int("KITTEN_MAX_CHUNK_CHARS", 400)
    max_text_chars = env_int("KITTEN_MAX_TEXT_CHARS", 0)


_engine = None
_voice_lookup: dict[str, str] = {}
_voice_names: list[str] = []
_preprocessors: dict[tuple, object] = {}
# The ONNX session is thread-safe, but the shared phonemizer is serialized here
# so concurrent requests cannot interleave inside it.
_lock = threading.Lock()


class NormalizationOptions(BaseModel):
    """Per-request overrides for the text preprocessing pipeline.

    A field left unset keeps the server default. These apply only when
    `clean_text` is true.
    """

    model_config = ConfigDict(extra="forbid")

    lowercase: Optional[bool] = None
    replace_numbers: Optional[bool] = None
    replace_floats: Optional[bool] = None
    expand_contractions: Optional[bool] = None
    expand_model_names: Optional[bool] = None
    expand_ordinals: Optional[bool] = None
    expand_percentages: Optional[bool] = None
    expand_currency: Optional[bool] = None
    expand_time: Optional[bool] = None
    expand_ranges: Optional[bool] = None
    expand_units: Optional[bool] = None
    expand_scale_suffixes: Optional[bool] = None
    expand_scientific_notation: Optional[bool] = None
    expand_fractions: Optional[bool] = None
    expand_decades: Optional[bool] = None
    expand_phone_numbers: Optional[bool] = None
    expand_ip_addresses: Optional[bool] = None
    normalize_leading_decimals: Optional[bool] = None
    expand_roman_numerals: Optional[bool] = None
    remove_urls: Optional[bool] = None
    remove_emails: Optional[bool] = None
    remove_html: Optional[bool] = None
    remove_hashtags: Optional[bool] = None
    remove_mentions: Optional[bool] = None
    remove_punctuation: Optional[bool] = None
    remove_stopwords: Optional[bool] = None
    stopwords: Optional[list[str]] = None
    normalize_unicode: Optional[bool] = None
    remove_accents: Optional[bool] = None
    remove_extra_whitespace: Optional[bool] = None


class SynthesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="Text to speak.")
    voice: Optional[str] = Field(
        default=None, description="Voice name such as Bruno, or a raw id such as expr-voice-3-m."
    )
    speed: Optional[float] = Field(default=None, ge=0.1, le=5.0)
    format: Optional[FormatName] = None
    sample_rate: Optional[int] = Field(default=None, ge=8000, le=48000)
    gain_db: Optional[float] = Field(default=None, ge=-40.0, le=20.0)
    clean_text: Optional[bool] = None
    max_chunk_chars: Optional[int] = Field(default=None, ge=50, le=2000)
    normalization: Optional[NormalizationOptions] = None


class NormalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    locale: str = "en-US"
    return_spans: bool = False
    normalization: Optional[NormalizationOptions] = None


class SpeechRequest(BaseModel):
    """Body of the OpenAI-compatible speech endpoint."""

    model_config = ConfigDict(extra="ignore")

    input: str = Field(min_length=1)
    model: Optional[str] = None
    voice: Optional[str] = None
    response_format: Optional[str] = None
    speed: Optional[float] = Field(default=None, ge=0.1, le=5.0)
    stream_format: Optional[str] = None


def load_model() -> None:
    """Load the model and build the voice lookup table."""
    global _engine, _voice_lookup, _voice_names

    from kittentts import KittenTTS

    model = KittenTTS(Defaults.model, cache_dir=Defaults.cache_dir, backend=Defaults.backend)
    _engine = model.model

    _voice_names = list(_engine.all_voice_names)
    _voice_lookup = {name.lower(): raw for name, raw in _engine.voice_aliases.items()}
    _voice_lookup.update({raw.lower(): raw for raw in _engine.available_voices})

    resolve_voice(Defaults.voice)
    require_format(Defaults.output_format)
    print(f"[kitten] model ready: {Defaults.model} voices={_voice_names}", flush=True)


def resolve_voice(voice: str) -> str:
    """Return the raw voice id for a friendly name or raw id."""
    raw = _voice_lookup.get(voice.strip().lower())
    if raw is None:
        raise HTTPException(
            400, f"Unknown voice {voice!r}. Choose from {_voice_names} or {_engine.available_voices}."
        )
    return raw


def require_format(name: str) -> str:
    name = name.strip().lower()
    if name not in FORMATS:
        raise HTTPException(400, f"Unknown format {name!r}. Choose from {sorted(FORMATS)}.")
    return name


def get_preprocessor(options: Optional[NormalizationOptions]):
    """Build (and cache) a TextPreprocessor for the requested flag set."""
    from kittentts.preprocess import TextPreprocessor

    config = dict(PREPROCESSOR_DEFAULTS)
    stopwords = None
    if options is not None:
        for field, value in options.model_dump(exclude_none=True).items():
            if field == "stopwords":
                stopwords = set(value)
            else:
                config[field] = value

    key = (tuple(sorted(config.items())), tuple(sorted(stopwords)) if stopwords else None)
    if key not in _preprocessors:
        _preprocessors[key] = TextPreprocessor(stopwords=stopwords, **config)
    return _preprocessors[key]


def prepare(request: SynthesisRequest) -> tuple[list[str], str, float]:
    """Validate a request and split its text into chunks ready for the model.

    Every rejection happens here, before a streaming response has started and
    committed its status code.
    """
    from kittentts.preprocess import chunk_text

    text = request.text
    if Defaults.max_text_chars and len(text) > Defaults.max_text_chars:
        raise HTTPException(413, f"text exceeds the {Defaults.max_text_chars} character limit.")

    clean = Defaults.clean_text if request.clean_text is None else request.clean_text
    if clean:
        text = get_preprocessor(request.normalization)(text)

    voice = resolve_voice(request.voice or Defaults.voice)
    speed = Defaults.speed if request.speed is None else request.speed
    max_chunk = request.max_chunk_chars or Defaults.max_chunk_chars

    pieces = chunk_text(text, max_len=max_chunk)
    if not pieces:
        raise HTTPException(400, "text contains nothing to speak after normalization.")
    return pieces, voice, speed


def generate_chunks(pieces: list[str], voice: str, speed: float) -> Iterator[np.ndarray]:
    """Yield one 24 kHz audio array per text chunk."""
    for piece in pieces:
        with _lock:
            audio = _engine.generate_single_chunk(piece, voice, speed)
        yield np.asarray(audio, dtype=np.float32).reshape(-1)


def apply_gain(audio: np.ndarray, gain_db: float) -> np.ndarray:
    if gain_db:
        audio = audio * (10.0 ** (gain_db / 20.0))
    return np.clip(audio, -1.0, 1.0)


def encode(audio: np.ndarray, sample_rate: int, output_format: str) -> bytes:
    container, subtype, _ = FORMATS[output_format]
    if container is None:
        return (audio * 32767.0).astype("<i2").tobytes()
    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format=container, subtype=subtype)
    return buffer.getvalue()


class StreamEncoder:
    """Encodes audio incrementally, returning only the newly produced bytes.

    Writers such as WAV record the total length in a header they rewrite when
    the file closes. A streamed response has already sent that header, so the
    length fields are set to "unknown" on the way out instead.
    """

    def __init__(self, sample_rate: int, output_format: str) -> None:
        container, subtype, _ = FORMATS[output_format]
        self.raw = container is None
        self.patch_wav = output_format == "wav"
        if self.raw:
            return
        self.buffer = io.BytesIO()
        self.position = 0
        self.writer = sf.SoundFile(
            self.buffer,
            mode="w",
            samplerate=sample_rate,
            channels=1,
            format=container,
            subtype=subtype,
        )

    def _drain(self) -> bytes:
        data = self.buffer.getvalue()
        block = data[self.position :]
        self.position = len(data)
        if self.patch_wav and block:
            block = self._unknown_lengths(block)
            self.patch_wav = False
        return block

    @staticmethod
    def _unknown_lengths(header: bytes) -> bytes:
        patched = bytearray(header)
        if patched[:4] == b"RIFF":
            patched[4:8] = UNKNOWN_SIZE
        data_at = patched.find(b"data")
        if data_at != -1:
            patched[data_at + 4 : data_at + 8] = UNKNOWN_SIZE
        return bytes(patched)

    def encode(self, audio: np.ndarray) -> bytes:
        if self.raw:
            return (audio * 32767.0).astype("<i2").tobytes()
        self.writer.write(audio)
        self.writer.flush()
        return self._drain()

    def close(self) -> bytes:
        if self.raw:
            return b""
        self.writer.close()
        return self._drain()


def synthesize(request: SynthesisRequest) -> Response:
    """Synthesize the whole request, then return it as one response."""
    output_format = require_format(request.format or Defaults.output_format)
    sample_rate = request.sample_rate or Defaults.sample_rate
    gain_db = Defaults.gain_db if request.gain_db is None else request.gain_db

    pieces, voice, speed = prepare(request)
    audio = np.concatenate(list(generate_chunks(pieces, voice, speed)), axis=-1)
    if sample_rate != MODEL_SAMPLE_RATE:
        audio = soxr.resample(audio, MODEL_SAMPLE_RATE, sample_rate)
    audio = apply_gain(audio, gain_db)

    body = encode(audio, sample_rate, output_format)
    media_type = FORMATS[output_format][2]
    if output_format == "pcm":
        media_type = f"audio/L16; rate={sample_rate}; channels=1"
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Length": str(len(body)),
            "X-Sample-Rate": str(sample_rate),
        },
    )


def synthesize_stream(request: SynthesisRequest) -> StreamingResponse:
    """Synthesize chunk by chunk, sending audio as the model produces it."""
    output_format = require_format(request.format or Defaults.output_format)
    sample_rate = request.sample_rate or Defaults.sample_rate
    gain_db = Defaults.gain_db if request.gain_db is None else request.gain_db

    # Validate before returning the response, so a bad request fails with a
    # status code instead of an empty 200 that has already been committed.
    pieces, voice, speed = prepare(request)

    def body() -> Iterator[bytes]:
        chunks = generate_chunks(pieces, voice, speed)
        encoder = StreamEncoder(sample_rate, output_format)
        resampler = None
        if sample_rate != MODEL_SAMPLE_RATE:
            resampler = soxr.ResampleStream(MODEL_SAMPLE_RATE, sample_rate, 1, dtype="float32")
        for audio in chunks:
            if resampler is not None:
                audio = resampler.resample_chunk(audio)
                if not audio.size:
                    continue
            block = encoder.encode(apply_gain(audio, gain_db))
            if block:
                yield block
        if resampler is not None:
            tail = resampler.resample_chunk(np.zeros(0, dtype=np.float32), last=True)
            if tail.size:
                block = encoder.encode(apply_gain(tail, gain_db))
                if block:
                    yield block
        tail = encoder.close()
        if tail:
            yield tail

    media_type = FORMATS[output_format][2]
    if output_format == "pcm":
        media_type = f"audio/L16; rate={sample_rate}; channels=1"
    return StreamingResponse(
        body(),
        media_type=media_type,
        headers={"X-Sample-Rate": str(sample_rate), "Cache-Control": "no-store"},
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    load_model()
    yield


app = FastAPI(
    title="Kitten TTS",
    description="Text-to-speech over HTTP, with every KittenTTS parameter exposed per request.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    """Report whether the model is loaded and which defaults are in force."""
    return {
        "ok": _engine is not None,
        "model": Defaults.model,
        "backend": Defaults.backend or "auto",
        "voices": _voice_names,
        "model_sample_rate": MODEL_SAMPLE_RATE,
        "formats": sorted(FORMATS),
        "defaults": {
            "voice": Defaults.voice,
            "speed": Defaults.speed,
            "format": Defaults.output_format,
            "sample_rate": Defaults.sample_rate,
            "gain_db": Defaults.gain_db,
            "clean_text": Defaults.clean_text,
            "max_chunk_chars": Defaults.max_chunk_chars,
            "max_text_chars": Defaults.max_text_chars or None,
        },
    }


@app.get("/voices")
def voices() -> dict:
    """List every voice, with the raw identifier each name maps to."""
    return {
        "default": Defaults.voice,
        "voices": [
            {"name": name, "id": _engine.voice_aliases[name]}
            for name in _voice_names
            if name in _engine.voice_aliases
        ],
        "openai_aliases": OPENAI_VOICES,
    }


@app.post("/tts", response_class=Response, responses={200: {"content": {"audio/wav": {}}}})
def tts(request: SynthesisRequest) -> Response:
    """Synthesize speech and return the complete audio file."""
    return synthesize(request)


@app.post("/tts/stream", response_class=Response, responses={200: {"content": {"audio/wav": {}}}})
def tts_stream(request: SynthesisRequest) -> StreamingResponse:
    """Synthesize speech and stream the audio as it is produced."""
    return synthesize_stream(request)


@app.post("/normalize")
def normalize(request: NormalizeRequest) -> dict:
    """Show what the text becomes before it reaches the model."""
    from kittentts import normalize_text

    if request.locale.lower() not in {"en-us", "en"}:
        raise HTTPException(400, f"Unsupported locale {request.locale!r}. Only en-US is supported.")

    import dataclasses

    result = normalize_text(request.text, locale=request.locale, return_spans=request.return_spans)
    payload: dict = {"preprocessed": get_preprocessor(request.normalization)(request.text)}
    if request.return_spans:
        payload["normalized"] = result.text
        payload["spans"] = [dataclasses.asdict(span) for span in result.spans]
    else:
        payload["normalized"] = result
    return payload


@app.post(
    "/v1/audio/speech",
    response_class=Response,
    responses={200: {"content": {"audio/mpeg": {}}}},
)
def openai_speech(request: SpeechRequest) -> Response:
    """Synthesize speech using the OpenAI speech API request format."""
    output_format = (request.response_format or "mp3").strip().lower()
    if output_format == "aac":
        raise HTTPException(400, "aac is not supported. Use mp3, opus, flac, wav, or pcm.")

    voice = request.voice or Defaults.voice
    voice = OPENAI_VOICES.get(voice.strip().lower(), voice)

    synthesis = SynthesisRequest(
        text=request.input,
        voice=voice,
        speed=request.speed,
        format=require_format(output_format),
    )
    if request.stream_format == "audio":
        return synthesize_stream(synthesis)
    return synthesize(synthesis)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=env_int("PORT", 8788), log_level="info")


if __name__ == "__main__":
    main()
