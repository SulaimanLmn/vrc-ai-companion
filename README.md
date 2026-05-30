# Companion

Real-time AI voice companion for VRChat with local wake word detection, speech recognition, and text-to-speech.

## Architecture

```
Mic audio (user's PC / loopback)
        │
        ▼
  ┌──────────────────────────────┐
  │  Wake word detection          │
  │  ┌──────┬──────┬──────────┐  │
  │  │ Vosk │ VAD  │ openWake │  │
  │  │ (any │(text│ Word     │  │
  │  │phrase│filter)│(model)  │  │
  │  └──────┴──────┴──────────┘  │
  └──────────────┬───────────────┘
                 │  wake word detected
                 ▼
  ┌──────────────────────────────┐
  │  Record → VAD → Azure STT    │
  │  (ring buffer prepends audio)│
  └──────────────┬───────────────┘
                 │  transcribed text
                 ▼
  ┌─────────────────┐     ┌──────────────────┐
  │  LLM (OpenAI-   │────▶│  Azure TTS       │
  │  compatible)    │     │  → speaker output│
  │                 │     │  → VRChat mic    │
  └────────┬────────┘     └────────┬─────────┘
           │                       │
           ▼                       ▼
  ┌─────────────────┐     ┌──────────────────┐
  │  VRChat OSC     │     │  Web UI          │
  │  ChatBox        │     │  (sidebar, chat, │
  │  word-by-word   │     │   tests, debug)  │
  └─────────────────┘     └──────────────────┘
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
pip install vosk            # for Vosk mode (~40 MB auto-downloads)
pip install openwakeword    # for openWakeWord mode (optional, requires model)
```

You'll also need:
- **[VB-CABLE](https://vb-audio.com/Cable/)** or **[Voicemeeter Banana](https://vb-audio.com/Voicemeeter/banana.htm)** — route TTS audio into VRChat mic
- **Azure Speech SDK** — included in `requirements.txt`
- **PyAudio** — included in `requirements.txt`

### 2. Configure

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

Edit `.env` with your:
- Azure Speech key & region (for STT + TTS)
- LLM API key, base URL, and model (any OpenAI-compatible API)
- `WAKE_KEYWORD` — trigger phrase (used by Vosk keyphrase or VAD text filter)
- `USE_WAKE_WORD` — detection mode: `vosk`, `vad`, or `openwakeword`
- VRChat OSC settings
- Audio device indices (run `python main.py --list-devices`)

### 3. Enable OSC in VRChat

- VRChat Settings → OSC → Enable
- Default port: `9000`

### 4. Run

```bash
python main.py
```

Open `http://localhost:5000` in your browser.

### Usage

| Control | Action |
|---------|--------|
| **Enable** button | Turn the system on/off |
| **Space** (hold) | Push-to-talk (hold spacebar) |
| **E** key | Toggle enable/disable |
| Text input | Manually type messages |

---

## Wake Word Detection

Three modes available:

| Mode | `USE_WAKE_WORD` | How it works | Required |
|------|-----------------|-------------|----------|
| **Vosk** | `vosk` | Keyphrase spotting via Vosk ASR — detects any phrase, no extra model needed | `pip install vosk` (model auto-downloads) |
| **VAD + text filter** | `vad` | Energy amplitude triggers recording, then Azure transcribes — only passes text containing `WAKE_KEYWORD` | Nothing extra |
| **openWakeWord** (recommended) | `openwakeword` | Classifier model scores audio frames, Silero VAD gates false positives | `pip install openwakeword` + your `.onnx`/`.tflite` model in `models/openwakeword/` |

Set `USE_WAKE_WORD` in settings or `.env`. Changes require restart.

---

## Audio Setup

### STT (Speech-to-Text) — what the AI hears

The AI listens through a PyAudio stream. Three paths depending on the mode:

- **Vosk**: detects keyword locally via Vosk → records utterance → sends one Azure STT call
- **VAD**: amplitude threshold triggers recording → Azure STT → text filter checks for `WAKE_KEYWORD`
- **openWakeWord**: classifier scores audio frames → triggers on high score → records → Azure STT

```bash
# List available input devices to find your index
python main.py --list-devices
```

### TTS (Text-to-Speech) — where the AI speaks

By default, TTS plays through your Windows default speaker. To route TTS audio into VRChat (so other players hear the AI), set a **WASAPI device UUID** in `.env`:

```bash
# Find WASAPI UUID for a device
pip install comtypes pycaw
python resolve_devices.py  <index>
# Copy the UUID into .env as TTS_OUTPUT_DEVICE_UUID
pip uninstall comtypes pycaw psutil -y
```

### Voicemeeter Setup (Recommended)

For full audio control, use **Voicemeeter Banana** to route audio between all components.

| Component | Set its device to |
|---|---|
| **VRChat audio output** (Settings → Audio) | `Voicemeeter VAIO` |
| **VRChat mic input** (Settings → Audio) | `Voicemeeter Out B1` |
| **TTS output** (`.env`: `TTS_OUTPUT_DEVICE_UUID`) | `Line 1 (Virtual Audio Cable)` → Voicemeeter captures it |
| **STT input** (`.env`: `AUDIO_DEVICE_INDEX`) | `Voicemeeter Out B2` |

#### Voicemeeter Matrix

```
                    HW1 (Mic)   VAIO (VRChat)   VAIO3 (TTS via Line 1)
                    ─────────   ─────────────   ─────────────────────
  A1 (Headphones)     ●            ●                 ○
  B1 (VRChat mic)     ●            ○                 ●
  B2 (→ STT)          ●            ●                 ○
```

### Simple VB-CABLE Setup (Alternative)

1. Set Windows default playback device to `CABLE Input (VB-Audio Virtual Cable)`
2. Set `TTS_OUTPUT_DEVICE_UUID` to the UUID of `CABLE Input`
3. Set VRChat microphone to `CABLE Output (VB-Audio Virtual Cable)`

---

## Web Interface

The web UI (`http://localhost:5000`) has a sidebar with three main pages:

| Page | Contents |
|------|----------|
| **Conversation** | Chat log with message history, text input, export/clear buttons |
| **Tests** | Test buttons for LLM, STT, TTS, and wake word, each with result area |
| **Debug** | Live console output from the backend — shows all logs, errors, latency markers |

The top bar shows a **status dot** (green = on, red = off) with a **state tag** (ON / LISTENING / THINKING / SPEAKING), a **live mic level meter**, and the Enable/Disable toggle.

**Settings** opens as a slide-out panel from the right. The Wake Word Detection dropdown offers three modes; in openWakeWord mode, a model selector appears instead of the trigger phrase text field. Changes that require a restart show a warning in the save notification.

---

## Components

| File | Purpose |
|------|---------|
| `main.py` | Main orchestrator (`Companion` class) + entry point |
| `stt.py` | `WakeWordSTT` (Vosk / VAD / openWakeWord + PyAudio + Azure) and legacy `AzureSTT` |
| `llm_client.py` | LLM client (OpenAI-compatible API) |
| `tts.py` | Azure TTS with queuing and WASAPI UUID routing |
| `vrchat_osc.py` | VRChat OSC ChatBox integration |
| `config.py` | Configuration from .env |
| `web_ui/` | Flask web interface (sidebar, chat log, tests, debug console) |
| `resolve_devices.py` | Helper to find WASAPI device UUIDs for TTS routing |
| `CLI_FLAGS.md` | Documentation for all CLI flags |

## Configuration Reference

### `.env` settings

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_SPEECH_KEY` | — | Azure Speech subscription key |
| `AZURE_SPEECH_REGION` | — | Azure region (dropdown in settings) |
| `LLM_API_KEY` | — | LLM API key |
| `LLM_BASE_URL` | — | LLM endpoint URL |
| `LLM_MODEL` | — | Model name (dropdown or text input) |
| `VRC_CHATBOX_IP` | `127.0.0.1` | VRChat OSC IP |
| `VRC_CHATBOX_PORT` | `9000` | VRChat OSC port |
| `AUDIO_DEVICE_INDEX` | `-1` | STT input device index |
| `STT_SILENCE_THRESHOLD` | `500` | Amplitude threshold for speech |
| `STT_SILENCE_CUTOFF_SEC` | `2.0` | Seconds of silence before transcribing |
| `TTS_OUTPUT_DEVICE_UUID` | `""` | WASAPI UUID for TTS output |
| `TTS_PITCH` | `0` | TTS pitch adjustment (%) |
| `SYSTEM_PROMPT` | — | AI personality prompt |
| `WEB_HOST` | `0.0.0.0` | Web UI host |
| `WEB_PORT` | `5000` | Web UI port |
| `WAKE_KEYWORD` | `computer` | Trigger phrase (Vosk keyphrase / VAD text filter) |
| `USE_WAKE_WORD` | `vosk` | Detection mode: `vosk`, `vad`, or `openwakeword` |
| `OWW_MODEL` | `""` | openWakeWord model name (e.g. `Amelia` for `Amelia.onnx`) |
| `VISION_TRIGGER_PHRASE` | `look at this` | Phrase to trigger screen capture |
| `VISION_CAPTURE_WINDOW` | `VRChat` | Window title to capture for vision |
| `LLM_MAX_TOKENS` | `150` | Max response tokens |
| `LLM_MAX_HISTORY` | `5` | Past exchanges to remember (0 = unlimited) |

## Tips

- **Lower latency**: Use a fast LLM model
- **Reduce `LLM_MAX_TOKENS`** for shorter, faster responses
- **Reduce `STT_SILENCE_CUTOFF_SEC`** (e.g., `1.0`) for faster detection
- **Run `python main.py --list-devices`** to find audio device indices
- **Test LLM**: Click "Test LLM" in the web UI to verify your connection

## Troubleshooting

- **LLM not responding**: Check your API key and base URL in settings
- **Wake word not detected (Vosk)**: Run `pip install vosk` — model auto-downloads on first run
- **Wake word not detected (openWakeWord)**: Place your `.onnx` or `.tflite` file in `models/openwakeword/` and select it in settings
- **False wake triggers**: Increase `STT_SILENCE_THRESHOLD` or switch to a different detection mode
- **STT not picking up speech**: Lower `STT_SILENCE_THRESHOLD` (try 200-300)
- **TTS not heard in VRChat**: Set `TTS_OUTPUT_DEVICE_UUID` to a virtual cable device UUID
- **ChatBox not showing**: Verify OSC is enabled in VRChat Settings (port 9000)
- **Azure errors**: Verify `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` are correct

## License

MIT
