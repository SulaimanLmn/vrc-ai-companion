# Project Architecture

## Purpose

An AI voice companion for **VRChat**. Listens via a microphone, detects a wake word locally via Vosk, records the full utterance, transcribes it with Azure STT, generates a response via any OpenAI-compatible LLM, and speaks back through the user's microphone while displaying the text in VRChat's chatbox.

## The Flow

```
Mic / desktop audio
        │
        ▼
┌───────────────────────────────────────────┐
│  1. WakeWordSTT (stt.py)                  │
│                                           │
│  ┌─────────┐    ┌──────────────────────┐  │
│  │ PyAudio │    │ Ring buffer (~1.5s)  │  │
│  │ stream  │───▶│ (keeps recent audio) │  │
│  └────┬────┘    └──────────────────────┘  │
│       │                                    │
│       ▼   energy gate (skip silence)       │
│  ┌─────────┐                               │
│  │  Vosk   │  offline keyphrase spotting   │
│  │ (16kHz) │  "computer", "hey vox", etc.  │
│  └────┬────┘                               │
│       │  keyword detected                  │
│       ▼                                    │
│  ┌──────────────────────────────────┐      │
│  │ Continue recording → VAD silence │      │
│  │ → resample → Azure STT           │      │
│  │ → callback(text)                 │      │
│  └──────────────────────────────────┘      │
└────────────────────┬──────────────────────┘
                     │  transcribed text
                     ▼
┌───────────────────────────────────────────┐
│  2. LLM (llm_client.py)                    │
│     • Sends text + history + system prompt │
│     • Blocks until full response received  │
│     • OpenAI-compatible API                │
│     • Also supports streaming + vision     │
└────────────────────┬──────────────────────┘
                     │  AI response text
                     ▼
┌───────────────────────────────────────────┐
│  3. OUTPUT — Dual Channel                   │
│     ┌────────────────┐  ┌────────────────┐ │
│     │ 3a. TTS (Azure)│  │ 3b. OSC ChatBox│ │
│     │ • enqueue()    │  │ • stream_text()│ │
│     │ • async synth  │  │ • word-by-word │ │
│     │ • WASAPI UUID  │  │ • 144-char     │ │
│     │   device route │  │   splitting    │ │
│     │ • pitch SSML   │  │ • typing ind.  │ │
│     └────────────────┘  └────────────────┘ │
└─────────────────────────────────────────────┘
```

## State Machine

```
OFF ──[Enable]──► ON (Vosk listening for keyword)
                      │
                      │  wake word detected
                      ▼
                  RECORDING (VAD listening)
                      │
                      │  silence detected
                      ▼
                TRANSCRIBING (Azure STT)
                      │
                      │  text received
                      ▼
                THINKING (LLM processing)
                      │
                      ▼
                SPEAKING (TTS playing + OSC streaming)
                      │
                      │  TTS done
                      ▼
                  ON (back to listening)
```

## Components

### `stt.py` — WakeWordSTT

- Single PyAudio stream at device's native sample rate
- Ring buffer keeps ~1.5s of recent audio
- Vosk processes 16kHz-resampled frames (energy gate: silent frames skipped)
- On keyword match: prepend ring buffer, continue recording with VAD, trim silence, resample to 16kHz, send to Azure STT
- Vosk state reset after each cycle + 3s cooldown prevents re-trigger
- Falls back to energy-VAD (amplitude threshold) when Vosk is unavailable

### `llm_client.py` — LLM Client

- OpenAI-compatible API (works with any provider: OpenAI, local, cloud, etc.)
- Conversation history (configurable depth, 0 = unlimited)
- `chat()` — blocking full response
- `chat_stream()` — streaming tokens via callback
- `chat_with_image()` — text + image input for vision

### `tts.py` — AzureTTS

- Single persistent SpeechSynthesizer (kept alive to avoid WASAPI driver corruption)
- Queue-based: `enqueue(text)` adds to play queue
- WASAPI UUID device routing via `AudioOutputConfig(device_name=uuid)`
- SSML with configurable voice and pitch
- Callbacks: `on_speaking_start`, `on_speaking_end`

### `vrchat_osc.py` — ChatBox

- OSC client for VRChat chatbox
- `stream_text()` — word-by-word incremental display with auto-calculated timing
- 144-char message splitting at sentence boundaries
- Typing indicator (`/chatbox/typing`)
- UDP reordering fix: final authoritative send per chunk

### `web_ui/` — Flask Dashboard

- Sidebar with three pages: Conversation, Tests, Debug
- Conversation: chat log with message history, input field, export/clear
- Tests: dedicated test cards for LLM, STT, TTS, and wake word
- Debug: live console output streamed from the backend
- Top bar: status dot + state tag, live mic level meter, enable toggle
- Settings panel: slide-out from right with live device dropdowns, sliders, text areas, password fields with eye toggle
- Keyboard shortcuts: Space (PTT), E (toggle), ? (help)
- Toast notifications for save confirmations and test results

## Key Configuration (.env)

| Variable | Default | Notes |
|----------|---------|-------|
| `AZURE_SPEECH_KEY` | — | Shared by STT and TTS |
| `AZURE_SPEECH_REGION` | `eastasia` | Azure region |
| `LLM_API_KEY` | — | LLM API key |
| `LLM_BASE_URL` | `https://opencode.ai/zen/go/v1` | LLM endpoint |
| `LLM_MODEL` | `mimo-v2.5` | LLM model |
| `WAKE_KEYWORD` | `computer` | Vosk wake word (empty = VAD fallback) |
| `AUDIO_DEVICE_INDEX` | `-1` | PyAudio input device index |
| `STT_SILENCE_THRESHOLD` | `500` | Amplitude threshold |
| `STT_SILENCE_CUTOFF_SEC` | `2.0` | Silence wait before transcribing |
| `TTS_OUTPUT_DEVICE_UUID` | `""` | WASAPI UUID for TTS output |
| `TTS_PITCH` | `0` | TTS pitch adjustment (%) |
| `SYSTEM_PROMPT` | — | AI personality |
| `LLM_MAX_TOKENS` | `150` | Max response tokens |
| `LLM_MAX_HISTORY` | `5` | Past exchanges to remember (0 = unlimited) |
| `VISION_TRIGGER_PHRASE` | `look at this` | Trigger for screen capture |
| `VISION_CAPTURE_WINDOW` | `VRChat` | Window title for capture |

## Key Design Decisions

- **Vosk over Porcupine**: 100% open-source, no API key or signup required, runs entirely offline
- **Energy gate**: Silent frames are never fed to Vosk — prevents keyword hallucination in noise floor
- **Vosk reset on resume**: Clears recognition state after TTS pause so stale context doesn't trigger
- **Word-boundary keyword matching**: `\bphrase\b` regex, not substring — prevents accidental partial matches
- **Single persistent TTS synthesizer**: Destroying/recreating Azure SDK synthesizer corrupts WASAPI audio driver
- **WASAPI UUID for TTS**: Azure SDK ignores friendly device names; must use `{0.0.0.00000000}.{...}` format
- **Ring buffer prepend**: Wake word audio is included in the recording so Azure transcribes the full utterance including the trigger word (which is then stripped from the LLM input)
- **One Azure STT call per interaction**: Wake word gates the audio, so only real user speech incurs API cost
- **Generic LLM support**: Uses OpenAI-compatible API, works with any provider
- **Over-the-shoulder readable**: Web UI designed for quick status checks at a glance

## CLI Flags

See `CLI_FLAGS.md` for `--list-devices`, `--list-windows`, and `resolve_devices.py` usage.

## License

MIT
