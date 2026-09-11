# Kitten TTS API

A self-hosted text-to-speech API that runs on your own hardware. Kitten TTS
API wraps the open source [Kitten TTS model](https://github.com/KittenML/KittenTTS)
in an HTTP service, so you can turn text into natural speech from any
language or tool that can make a web request.

It runs on CPU. You do not need a GPU, an account, or an API key.

```bash
docker run -d -p 8788:8788 ghcr.io/gmkr/kitten-tts-api:latest

curl -s http://localhost:8788/tts \
  -H 'Content-Type: application/json' \
  -d '{"text": "Hello from Kitten."}' \
  -o hello.wav
```

## Why run your own text-to-speech API

| | Kitten TTS API | Hosted speech APIs |
|---|---|---|
| Cost per character | None after setup | Metered, and it grows with usage |
| Your text | Stays on your machine | Sent to a third party |
| Rate limits | Your hardware | Set by the provider |
| Network access | Runs offline after the build | Required for every request |
| Model size | 25 MB to 80 MB | Not applicable |
| Voices | 8 built-in | Varies by provider |

Run it when you care about cost at volume, about keeping text private, or
about working without a network connection. A hosted API remains the better
choice when you need many languages, voice cloning, or speech that is
indistinguishable from a recording.

## What you can build

- **Narration and audiobooks.** Stream long passages and receive audio as it
  is produced, instead of waiting for the whole file.
- **Voice assistants and phone systems.** Request 8 kHz audio
  for telephony, or raw samples you can feed to an audio device.
- **Accessibility features.** Read page content aloud without sending it to a
  third party.
- **Notifications and alerts.** Generate spoken status messages from a script
  or a cron job.
- **A drop-in replacement for a hosted speech API.** Point an existing
  OpenAI-compatible client at this service. See [Use an OpenAI client](#use-an-openai-client).

## Requirements

- Docker with the Compose plugin.
- About 1 GB of disk space for the image and the model.

## Get started

### Run the published image

The fastest path is the prebuilt image. It carries the model already, so
nothing is downloaded at run time:

```bash
docker run -d --name kitten-tts -p 8788:8788 ghcr.io/gmkr/kitten-tts-api:latest
```

Images are published for `linux/amd64` and `linux/arm64`, so the same command
works on a cloud server, an AWS Graviton instance, a Raspberry Pi, and Apple
Silicon.

To pin a version instead of tracking the newest build, use a version tag:

```bash
docker run -d -p 8788:8788 ghcr.io/gmkr/kitten-tts-api:1.0.1 # x-release-please-version
```

| Tag | What you get |
|---|---|
| `latest` | The newest build from the `main` branch |
| `1`, `1.0`, `1.0.0` | A released version, at the precision you choose |
| `sha-<commit>` | One exact commit |

### Build it yourself

To build from source and start the service on port 8788, run:

```bash
docker compose up -d
```

The first build downloads the model, which takes a few minutes. Later starts
take seconds. The container restarts on its own unless you stop it.

To confirm the service is running, run:

```bash
curl http://localhost:8788/health
```

To stop the container, run:

```bash
docker compose down
```

## Make your first request

Send the text you want spoken. The response carries the audio:

```bash
curl -s http://localhost:8788/tts \
  -H 'Content-Type: application/json' \
  -d '{"text": "Hello from Kitten.", "voice": "Jasper", "speed": 1.1}' \
  -o hello.wav
```

To hear the result, open `hello.wav` in any audio player.

### From Python

```python
import requests

audio = requests.post(
    "http://localhost:8788/tts",
    json={"text": "Hello from Kitten.", "voice": "Luna", "format": "mp3"},
)
open("hello.mp3", "wb").write(audio.content)
```

### From JavaScript

```javascript
const response = await fetch("http://localhost:8788/tts", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ text: "Hello from Kitten.", voice: "Leo" }),
});
const audio = new Audio(URL.createObjectURL(await response.blob()));
audio.play();
```

## Explore the API in a browser

Open `http://localhost:8788/docs` to read the generated reference and send
test requests without writing any code.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Report model status and the active defaults |
| `GET` | `/voices` | List voices, their raw identifiers, and the OpenAI aliases |
| `POST` | `/tts` | Synthesize speech and return the complete file |
| `POST` | `/tts/stream` | Synthesize speech and stream audio as it is produced |
| `POST` | `/normalize` | Preview text normalization without synthesizing |
| `POST` | `/v1/audio/speech` | Synthesize speech using the OpenAI request format |
| `GET` | `/docs` | Browse the interactive API reference |

Open `http://localhost:8788/docs` to try every parameter in a browser.

### Check health

The `/health` response names the loaded model, the available voices and formats, and
the defaults that requests inherit:

```json
{
  "ok": true,
  "model": "KittenML/kitten-tts-mini-0.8",
  "backend": "auto",
  "voices": ["Bella", "Jasper", "Luna", "Bruno", "Rosie", "Hugo", "Kiki", "Leo"],
  "model_sample_rate": 24000,
  "formats": ["flac", "mp3", "ogg", "opus", "pcm", "wav"],
  "defaults": {
    "voice": "Bruno", "speed": 1.0, "format": "wav", "sample_rate": 24000,
    "gain_db": 0.0, "clean_text": true, "max_chunk_chars": 400, "max_text_chars": null
  }
}
```

### Synthesize speech

#### Request parameters

Omit a field to use the server default.

| Field | Type | Default | Description |
|---|---|---|---|
| `text` | string | Required | Text to speak. Must not be empty. |
| `voice` | string | `Bruno` | A voice name such as `Luna`, or a raw identifier such as `expr-voice-3-m`. Names are case-insensitive. |
| `speed` | number | `1.0` | Speech rate multiplier, from `0.1` to `5.0`. |
| `format` | string | `wav` | One of `wav`, `flac`, `ogg`, `opus`, `mp3`, or `pcm`. |
| `sample_rate` | integer | `24000` | Output rate in hertz, from `8000` to `48000`. |
| `gain_db` | number | `0.0` | Volume adjustment in decibels, from `-40` to `20`. |
| `clean_text` | boolean | `true` | Expand numbers, currencies, units, and abbreviations before synthesis. |
| `max_chunk_chars` | integer | `400` | Longest text chunk sent to the model at once, from `50` to `2000`. |
| `normalization` | object | `null` | Overrides for individual normalization steps. See [Control text normalization](#control-text-normalization). |

A request that names an unknown voice returns status 400. A request that
fails validation, such as a speed outside the allowed range or an
unrecognized field, returns status 422 with the offending field named.

#### Voices

| Name | Identifier | Name | Identifier |
|---|---|---|---|
| `Bella` | `expr-voice-2-f` | `Rosie` | `expr-voice-4-f` |
| `Jasper` | `expr-voice-2-m` | `Hugo` | `expr-voice-4-m` |
| `Luna` | `expr-voice-3-f` | `Kiki` | `expr-voice-5-f` |
| `Bruno` | `expr-voice-3-m` | `Leo` | `expr-voice-5-m` |

### Choose an output format

| Format | Media type | Notes |
|---|---|---|
| `wav` | `audio/wav` | Uncompressed 16-bit PCM. Works everywhere. |
| `flac` | `audio/flac` | Lossless, and about 40 percent of the WAV size. |
| `ogg` | `audio/ogg` | Vorbis in an Ogg container. |
| `opus` | `audio/ogg` | Opus in an Ogg container. The smallest output, and the best choice for streaming over a network. |
| `mp3` | `audio/mpeg` | The safest choice when you do not control the client. |
| `pcm` | `audio/L16` | Raw signed 16-bit little-endian samples, no header. Feed straight to an audio device. |

For roughly two seconds of speech, the formats produce about 117 KB for WAV,
49 KB for FLAC, 17 KB each for Ogg and MP3, and 11 KB for Opus.

### Set the sample rate

The model always generates at 24 kHz. When you ask for another rate, the
service resamples the audio with `soxr`, so pitch and duration stay correct.
Use `8000` for telephony, `16000` for speech recognition pipelines, and
`48000` for video production.

```bash
curl -s http://localhost:8788/tts \
  -H 'Content-Type: application/json' \
  -d '{"text": "Telephone quality.", "sample_rate": 8000}' \
  -o phone.wav
```

### Stream audio as it is produced

`POST /tts/stream` takes the same body as `/tts` but sends each chunk of
audio as soon as the model finishes it, rather than waiting for the whole
passage. For about a minute of speech, the first audio arrives in under a
second instead of after 35 seconds.

```bash
curl -sN http://localhost:8788/tts/stream \
  -H 'Content-Type: application/json' \
  -d '{"text": "A long passage of text.", "format": "opus"}' \
  -o narration.opus
```

All six formats stream. A streamed WAV file records its length as unknown,
because the true length is not known when the header is sent; players and
decoders read it as an open-ended stream.

### Control text normalization

When `clean_text` is true, the service rewrites the text before synthesis so
the model speaks numbers, currencies, and abbreviations the way a person
would. Pass a `normalization` object to change individual steps. Any step you
leave out keeps its default.

```bash
curl -s http://localhost:8788/tts \
  -H 'Content-Type: application/json' \
  -d '{
        "text": "Visit https://example.com for 50% off Chapter IV.",
        "normalization": {"remove_urls": false, "expand_roman_numerals": true}
      }' \
  -o promo.wav
```

**Expansion steps.** Each one defaults to `true` except
`expand_roman_numerals`, which defaults to `false`.

`replace_numbers`, `replace_floats`, `expand_contractions`,
`expand_model_names`, `expand_ordinals`, `expand_percentages`,
`expand_currency`, `expand_time`, `expand_ranges`, `expand_units`,
`expand_scale_suffixes`, `expand_scientific_notation`, `expand_fractions`,
`expand_decades`, `expand_phone_numbers`, `expand_ip_addresses`,
`normalize_leading_decimals`, `expand_roman_numerals`

**Removal steps.**

| Field | Default | Effect |
|---|---|---|
| `remove_urls` | `true` | Drop web addresses |
| `remove_emails` | `true` | Drop email addresses |
| `remove_html` | `true` | Drop HTML tags |
| `remove_hashtags` | `false` | Drop `#hashtags` |
| `remove_mentions` | `false` | Drop `@mentions` |
| `remove_punctuation` | `false` | Drop punctuation. Leave this off, because punctuation carries the prosody. |
| `remove_stopwords` | `false` | Drop the words listed in `stopwords` |
| `stopwords` | `null` | Array of words that `remove_stopwords` drops |

**Text shaping steps.**

| Field | Default | Effect |
|---|---|---|
| `lowercase` | `true` | Convert the text to lowercase |
| `normalize_unicode` | `true` | Apply Unicode normalization |
| `remove_accents` | `false` | Strip accent marks |
| `remove_extra_whitespace` | `true` | Collapse runs of whitespace |

### Preview normalization

`POST /normalize` shows what the text becomes without synthesizing anything.
The response reports both pipelines: `normalized` comes from the library
normalizer, and `preprocessed` comes from the same pipeline that `/tts` uses,
including any `normalization` overrides you send.

```bash
curl -s http://localhost:8788/normalize \
  -H 'Content-Type: application/json' \
  -d '{"text": "Dr. Rivera paid $12.50 at 3:05 p.m.", "return_spans": true}'
```

```json
{
  "preprocessed": "dr. rivera paid twelve dollars and fifty cents at three oh fivep.m.",
  "normalized": "Doctor Rivera paid twelve dollars and fifty cents at three oh five p m.",
  "spans": [{"originalStartChar": 0, "originalEndChar": 3,
             "normalizedStartChar": 0, "normalizedEndChar": 6, "reason": "abbreviation"}]
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `text` | string | Required | Text to normalize |
| `locale` | string | `en-US` | Only `en-US` and `en` are supported |
| `return_spans` | boolean | `false` | Include the map from original to normalized character ranges |
| `normalization` | object | `null` | Overrides applied to the `preprocessed` result |

### Use an OpenAI client

`POST /v1/audio/speech` accepts the OpenAI speech request format, so a client
already written against that API works when you change its base address.

```bash
curl -s http://localhost:8788/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model": "tts-1", "input": "Hello.", "voice": "alloy", "response_format": "mp3"}' \
  -o hello.mp3
```

| Field | Default | Notes |
|---|---|---|
| `input` | Required | Text to speak |
| `model` | Ignored | Accepted so existing clients work unchanged |
| `voice` | `Bruno` | An OpenAI voice name or a Kitten voice name |
| `response_format` | `mp3` | `mp3`, `opus`, `flac`, `wav`, or `pcm`. A request for `aac` returns status 400. |
| `speed` | `1.0` | Speech rate multiplier |
| `stream_format` | Unset | Set to `audio` to stream the response |

OpenAI voice names map onto Kitten voices as follows: `alloy` to `Bruno`,
`ash` to `Jasper`, `ballad` to `Hugo`, `coral` to `Rosie`, `echo` to `Hugo`,
`fable` to `Jasper`, `nova` to `Luna`, `onyx` to `Leo`, `sage` to `Kiki`,
`shimmer` to `Bella`, and `verse` to `Leo`.

## Configuration

Set these environment variables on the service to change its defaults. A
request can override any of them except `KITTEN_MODEL`, `KITTEN_BACKEND`,
`KITTEN_CACHE_DIR`, `KITTEN_MAX_TEXT_CHARS`, and `PORT`.

| Variable | Default | Purpose |
|---|---|---|
| `KITTEN_MODEL` | `KittenML/kitten-tts-mini-0.8` | Hugging Face model ID |
| `KITTEN_BACKEND` | Unset | `cpu`, `cuda`, or `amd_gpu`. Unset lets ONNX Runtime pick. |
| `KITTEN_CACHE_DIR` | Unset | Directory for downloaded model files |
| `KITTEN_VOICE` | `Bruno` | Voice for requests that omit one |
| `KITTEN_SPEED` | `1.0` | Speech rate for requests that omit one |
| `KITTEN_FORMAT` | `wav` | Output format for requests that omit one |
| `KITTEN_SAMPLE_RATE` | `24000` | Output sample rate for requests that omit one |
| `KITTEN_GAIN_DB` | `0.0` | Volume adjustment for requests that omit one |
| `KITTEN_CLEAN_TEXT` | `true` | Normalize text for requests that omit the setting |
| `KITTEN_MAX_CHUNK_CHARS` | `400` | Chunk size for requests that omit one |
| `KITTEN_MAX_TEXT_CHARS` | `0` | Longest accepted text. `0` means no limit. Longer text returns status 413. |
| `PORT` | `8788` | Listen port |

The service validates this configuration at boot. An unusable value, such as
an unknown default voice or backend, stops the container with an explanatory
message rather than failing on the first request.

To change the port, edit the `ports` mapping in `compose.yaml`. Keep the
container port at `8788` and change only the host port.

## Choose a model

The build downloads and bakes in the selected model, so pass it as a build
argument:

```bash
docker compose build --build-arg KITTEN_MODEL=KittenML/kitten-tts-micro-0.8
docker compose up -d
```

Available models, from largest and highest quality to smallest:

| Model | Parameters | Size |
|---|---|---|
| `KittenML/kitten-tts-mini-0.8` | 80M | 80 MB (default) |
| `KittenML/kitten-tts-micro-0.8` | 40M | 41 MB |
| `KittenML/kitten-tts-nano-0.8-fp32` | 15M | 56 MB |
| `KittenML/kitten-tts-nano-0.8-int8` | 15M | 25 MB (reported issues; avoid) |

## Known behavior

- **Output varies between identical requests.** The model graph includes a
  random component, so the same text produces audio that differs slightly
  each time. This comes from the model itself, not from this service, and no
  seed parameter is available to control it.
- **The phonemizer runs one request at a time.** The service accepts
  concurrent requests and the ONNX session is thread-safe, but a lock
  serializes the shared phonemizer, so throughput does not scale with the
  number of clients.
- **Only English is supported.** The model and the normalizer both handle
  `en-US` alone.

## Point a client at another host

Any HTTP client works. Send a `POST` request to `/tts` with a JSON body and
read the audio from the response. Set the host and the port to the machine
that runs the container.

## Contributing

Report a problem or suggest a change by opening an issue. Pull requests are
welcome.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org).
The prefix decides how the change appears in the changelog and how the version
moves:

| Prefix | Example | Version effect |
|---|---|---|
| `feat:` | `feat: add opus output` | Minor |
| `fix:` | `fix: reject empty text` | Patch |
| `docs:`, `ci:`, `chore:` | `docs: clarify sample rates` | None |
| `feat!:` or a `BREAKING CHANGE:` footer | `feat!: drop the wav default` | Major |

Releases are automated. A release pull request stays open and collects every
change landed on `main`. Merging it writes the changelog, tags the commit,
publishes a GitHub release, and pushes the matching image tags to the
registry.

## License

This project is licensed under the [Apache License 2.0](LICENSE).

It builds on [KittenTTS](https://github.com/KittenML/KittenTTS) by KittenML,
which is licensed under the Apache License 2.0, and uses the
[Kitten TTS model weights](https://huggingface.co/KittenML), also licensed
under the Apache License 2.0. See [NOTICE](NOTICE) for attribution details.
