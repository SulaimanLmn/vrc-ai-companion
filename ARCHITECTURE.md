# Project Architecture

## Purpose

An AI voice companion for **VRChat**. Listens via a microphone, detects a wake word locally (Vosk keyphrase, energy VAD + text filter, or openWakeWord classifier), records the full utterance, transcribes it with Azure STT, generates a response via any OpenAI-compatible LLM, and speaks back through the user's microphone while displaying the text in VRChat's chatbox.

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
│       ▼   detection (one of three)         │
│  ┌──────┬───────┬──────────────────┐       │
│  │ Vosk │  VAD  │ openWakeWord     │       │
│  │ key- │ energy│ classifier       │       │
│  │phrase│thresh │ Silero VAD gate  │       │
│  │+text │ +text │ score > 0.6      │       │
│  │match │filter │                  │       │
│  └──────┴───────┴──────────────────┘       │
│       │  keyword detected                   │
│       ▼                                    │
│  ┌──────────────────────────────────┐      │
│  │ Continue recording → VAD silence │      │
│  │ → trim silence → resample →      │      │
│  │ → Azure STT → strip keyword      │      │
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
│     • Also supports vision                 │
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
OFF ──[Enable]──► ON (listening for wake word)
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
                      │  TTS done → 3s cooldown → reset model state
                      ▼
                  ON (back to listening)
```

## Components

### `stt.py` — WakeWordSTT

- Single PyAudio stream at device's native sample rate
- Ring buffer keeps ~1.5s of recent audio
- Three detection modes (set via `USE_WAKE_WORD`):
  - **vosk**: Vosk ASR checks partial text for keyword match (energy gate prevents silence processing)
  - **vad**: Amplitude threshold triggers recording, text filter checks for keyword after Azure STT
  - **openwakeword**: ONNX/TFLite classifier scores audio frames at 160ms intervals, Silero VAD (threshold 0.5) gates false positives, score > 0.6 triggers recording
- On any trigger: prepend ring buffer, continue recording with VAD, trim trailing silence, resample to 16kHz, send to Azure STT
- Keyword stripped from transcribed text: Vosk uses `WAKE_KEYWORD`, openWakeWord uses model filename (e.g. "Amelia")
- Vosk state and openWakeWord prediction buffer reset after each cycle + 3s cooldown prevents re-trigger
- 3s post-resume cooldown prevents TTS echo from triggering detection
- Falls back to energy-VAD when selected mode is unavailable

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
- Settings panel: slide-out from right with live device dropdowns, sliders, text areas, password fields; Wake Word Detection has three modes, model selector appears when openWakeWord is selected
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
| `WAKE_KEYWORD` | `computer` | Trigger phrase (Vosk keyphrase / VAD text filter) |
| `USE_WAKE_WORD` | `vosk` | Detection mode: `vosk`, `vad`, or `openwakeword` |
| `OWW_MODEL` | `""` | openWakeWord model name (e.g. `Amelia`) |
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

- **Three wake word modes** — Vosk (any phrase, no training), VAD (simple energy gate + text filter), openWakeWord (classifier + VAD gate, requires model)
- **openWakeWord model reset** — `model.reset()` clears the prediction buffer after each cycle so TTS echo doesn't accumulate across interactions
- **Silero VAD gate** — Built-in voice activity detector (threshold 0.5) prevents wake word detection on non-speech noise
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
