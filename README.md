# vrc-ai-companion

Real-time AI voice companion for VRChat with offline wake word, speech recognition, and text-to-speech.

## Architecture

```
Mic audio (user's PC / loopback)
        │
        ▼
  ┌─────────────────┐
  │  Vosk (offline) │  ← local wake word detection, no API key
  │  keyphrase      │      "computer", "next time", etc.
  │  spotting       │
  └────────┬────────┘
           │  wake word detected
           ▼
  ┌─────────────────┐
  │  Record → VAD   │  ← captures full utterance until silence
  │  → Azure STT    │      (ring buffer prepends wake word audio)
  └────────┬────────┘
           │  transcribed text
           ▼
  ┌─────────────────┐     ┌──────────────────┐
  │  LLM (OpenCode  │────▶│  Azure TTS       │
  │  Go, OpenAI,    │     │  → speaker output│
  │  etc.)          │     │  → VRChat mic    │
  └────────┬────────┘     └────────┬─────────┘
           │                       │
           ▼                       ▼
  ┌─────────────────┐     ┌──────────────────┐
  │  VRChat OSC     │     │  Web UI          │
  │  ChatBox        │     │  (status, config,│
  │  word-by-word   │     │   chat log)      │
  └─────────────────┘     └──────────────────┘
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
pip install vosk    # offline wake word (~40 MB model auto-downloads)
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
- LLM API key & model (OpenCode Go or any OpenAI-compatible API)
- `WAKE_KEYWORD` — the wake word phrase (e.g., `computer`, `next time`)
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

## Wake Word

The system uses **Vosk** for offline wake word detection — no API key, no signup, 100% private.

By default, it listens for `"computer"`. Set `WAKE_KEYWORD` in `.env` to any phrase you want (single or multi-word). The wake word gates recording: Azure STT is only called when the keyword is detected, saving API costs and reducing latency.

If `WAKE_KEYWORD` is empty, the system falls back to energy-based VAD (any loud sound triggers recording).

---

## Audio Setup

### STT (Speech-to-Text) — what the AI hears

The AI listens through a PyAudio stream. Two paths:

- **Wake word mode** (default): Vosk detects keyword locally → records utterance → sends one Azure STT call
- **Energy VAD fallback**: amplitude threshold triggers recording (used when Vosk model is unavailable)

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

### Voicemeeter Banana Setup (Recommended)

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
  A1 (Your TWS)       ●            ●                 ○
  B1 (VRChat mic)     ●            ○                 ●
  B2 (→ STT)          ●            ●                 ○
```

### Simple VB-CABLE Setup (Alternative)

1. Set Windows default playback device to `CABLE Input (VB-Audio Virtual Cable)`
2. Set `TTS_OUTPUT_DEVICE_UUID` to the UUID of `CABLE Input`
3. Set VRChat microphone to `CABLE Output (VB-Audio Virtual Cable)`

---

## ⚠️ Important: VRChat OSC / ChatBox Code

The file `vrchat_osc.py` handles all VRChat chatbox text streaming (word-by-word, 144-char limit, typing indicator). This code has been carefully tuned and tested.

**Do not modify `vrchat_osc.py` or the streaming logic in `main.py` without explicit permission.**

---

## Components

| File | Purpose |
|------|---------|
| `main.py` | Main orchestrator + entry point |
| `stt.py` | `WakeWordSTT` (Vosk + PyAudio + Azure) and legacy `AzureSTT` |
| `llm_client.py` | LLM client (OpenAI-compatible API) |
| `tts.py` | Azure TTS with queuing and WASAPI UUID routing |
| `vrchat_osc.py` | VRChat OSC ChatBox integration |
| `config.py` | Configuration from .env |
| `web_ui/` | Flask web interface (status, settings, chat log) |
| `resolve_devices.py` | Helper to find WASAPI device UUIDs for TTS routing |
| `CLI_FLAGS.md` | Documentation for all CLI flags |

## Configuration Reference

### `.env` settings

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_SPEECH_KEY` | — | Azure Speech subscription key |
| `AZURE_SPEECH_REGION` | — | Azure region (e.g., `eastasia`) |
| `OPENCODE_GO_API_KEY` | — | LLM API key |
| `OPENCODE_GO_BASE_URL` | — | LLM endpoint URL |
| `OPENCODE_GO_MODEL` | — | Model name |
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
| `WAKE_KEYWORD` | `computer` | Vosk wake word phrase (empty = disabled) |
| `VISION_TRIGGER_PHRASE` | `look at this` | Phrase to trigger screen capture |
| `VISION_CAPTURE_WINDOW` | `VRChat` | Window title to capture for vision |

## Tips

- **Lower latency**: Use a fast LLM model (`mimo-v2.5`, `deepseek-v4-flash`)
- **Reduce `LLM_MAX_TOKENS`** in `.env` for shorter, faster responses
- **Reduce `STT_SILENCE_CUTOFF_SEC`** (e.g., `1.0`) for faster detection
- **Run `python main.py --list-devices`** to find audio device indices
- **Test LLM**: Click "Test LLM" in the web UI to verify your connection

## Troubleshooting

- **LLM not responding**: Check `OPENCODE_GO_API_KEY` and `OPENCODE_GO_BASE_URL`
- **Wake word not detected**: Run `pip install vosk` — model auto-downloads on first run
- **False wake triggers**: Increase `STT_SILENCE_THRESHOLD` or use a less common keyword
- **STT not picking up speech**: Lower `STT_SILENCE_THRESHOLD` (try 200-300)
- **TTS not heard in VRChat**: Set `TTS_OUTPUT_DEVICE_UUID` to a virtual cable device UUID
- **ChatBox not showing**: Verify OSC is enabled in VRChat Settings (port 9000)
- **Azure errors**: Verify `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` are correct

## License

MIT
