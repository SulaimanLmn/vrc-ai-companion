# NeuroClone — AI VTuber for VRChat

A Neuro-sama-inspired AI companion for VRChat. Listens to nearby conversation, thinks, and responds via voice + VRChat ChatBox (OSC).

## Architecture

```
Microphone/Loopback
        │
        ▼
  ┌─────────────┐     ┌──────────┐     ┌──────────┐
  │ Azure STT   │────▶│   LLM    │────▶│ Azure    │
  │ (streaming) │     │(OpenCode │     │   TTS    │
  └─────────────┘     │    Go)   │     └────┬─────┘
                      └──────────┘          │
                                            ▼
                                     Virtual Audio Cable
                                            │
                                            ▼
                                     VRChat Microphone
                                            │
                                            ▼
                                     VRChat OSC ChatBox
```

## Quick Start

### 1. Install dependencies (on your Windows machine)

```bash
cd neuro-clone
pip install -r requirements.txt
```

You'll also need:
- **[VB-CABLE](https://vb-audio.com/Cable/)** or **[Voicemeeter Banana](https://vb-audio.com/Voicemeeter/banana.htm)** — route TTS audio into VRChat mic
- **Azure Speech SDK** — `pip install azure-cognitiveservices-speech`
- **PyAudio** — `pip install pyaudio` (may need [portaudio](https://www.portaudio.com/) on some systems)

### 2. Configure

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

Edit `.env` with your:
- Azure Speech key & region
- OpenCode Go API key & model
- VRChat OSC settings
- Audio device indices (see [Audio Setup](#audio-setup))

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
| **PTT** button | Toggle push-to-talk listening |
| **Space** (hold) | Push-to-talk (hold spacebar) |
| **E** key | Toggle enable/disable |
| Text input | Manually type messages |

---

## Audio Setup

### STT (Speech-to-Text) — what the AI hears

Two modes controlled by `STT_CAPTURE_MODE` in `.env`:

**`loopback` (default):** Captures ALL desktop audio playing through your speakers/headphones. The AI hears VRChat voices, game audio, YouTube, etc. Requires **Stereo Mix** enabled in Windows Sound settings (Recording tab → right-click → Show Disabled Devices → Enable Stereo Mix).

**`microphone`:** Captures from a specific input device set by `AUDIO_DEVICE_INDEX`. Use this with a virtual audio bus (e.g., Voicemeeter Out B2) so the AI only hears exactly what you route there.

```bash
# List available input devices to find your index
python main.py --list-devices
```

### TTS (Text-to-Speech) — where the AI speaks

By default, TTS plays through your Windows default speaker. To route TTS audio into VRChat (so other players hear the AI), you need to set a **WASAPI device UUID** in `.env`:

```bash
# Step 1: Install temporary dependencies to scan devices
pip install comtypes pycaw

# Step 2: List output devices with their WASAPI UUIDs
python resolve_devices.py

# Example output:
#   [ 25]  Headphone (Realtek(R) Audio)
#          UUID: {0.0.0.00000000}.{5ee1d5a6-4430-4849-b714-063c59158f6c}
#   [ 33]  Line 1 (Virtual Audio Cable)
#          UUID: {0.0.0.00000000}.{e68cc810-e3d0-4b56-96e5-039c7bce9fab}
#
# Or resolve a specific index:
python resolve_devices.py 33

# Step 3: Copy the UUID into .env
# TTS_OUTPUT_DEVICE_UUID={0.0.0.00000000}.{e68cc810-e3d0-4b56-96e5-039c7bce9fab}

# Step 4: Clean up (comtypes/pycaw not needed for normal operation)
pip uninstall comtypes pycaw psutil -y
```

**Note:** The UUID is a one-time setup. The main app never imports `comtypes`/`pycaw` — they are only used by the helper script in a separate process.

---

### Voicemeeter Banana Setup (Recommended)

For full audio control, use **Voicemeeter Banana** to route audio between all components.

#### Device mapping

| Component | Set its device to |
|---|---|
| **VRChat audio output** (Settings → Audio) | `Voicemeeter VAIO` |
| **VRChat mic input** (Settings → Audio) | `Voicemeeter Out B1` |
| **TTS output** (`.env`: `TTS_OUTPUT_DEVICE_UUID`) | `Line 1 (Virtual Audio Cable)` → Voicemeeter captures it |
| **STT input** (`.env`: `AUDIO_DEVICE_INDEX`) | `Voicemeeter Out B2` (or whichever bus feeds into STT) |

#### Voicemeeter Matrix

```
                    HW1 (Mic)   VAIO (VRChat)   VAIO3 (TTS via Line 1)
                    ─────────   ─────────────   ─────────────────────
  A1 (Your TWS)       ●            ●                 ○
  B1 (VRChat mic)     ●            ○                 ●
  B2 (→ STT)          ●            ●                 ○

  ● = routed (click ON)    ○ = not routed
```

#### What each person hears

| Person | Hears |
|---|---|
| **You** on TWS | Your mic + VRChat game audio (via A1) |
| **VRChat players** | Your mic + AI's voice (via B1) |
| **AI (STT)** | Your mic + VRChat voices (via B2) |

#### Important: TTS audio routing detail

TTS plays through `Line 1 (Virtual Audio Cable)` which is captured by Voicemeeter as an input. Route `Line 1` channel → **B1** (VRChat mic). Route `Line 1` channel → **A1** if you want to monitor the TTS output on your headphones.

---

### Simple VB-CABLE Setup (Alternative)

If you prefer VB-CABLE over Voicemeeter:

1. Set Windows default playback device to `CABLE Input (VB-Audio Virtual Cable)`
2. Set `TTS_OUTPUT_DEVICE_UUID` to the UUID of `CABLE Input (VB-Audio Virtual Cable)` (run `resolve_devices.py` to find it)
3. Set VRChat microphone to `CABLE Output (VB-Audio Virtual Cable)`
4. All audio (YouTube + TTS) goes through CABLE → Voicemeeter or direct to VRChat

---

### Finding device indices

```bash
python main.py --list-devices
```

Shows both output devices (for TTS) and input devices (for STT) with their PyAudio indices.

---

## ⚠️ Important: VRChat OSC / ChatBox Code

The file `vrchat_osc.py` handles all VRChat chatbox text streaming (word-by-word appearance, 144-char limit splitting, typing indicator). This code has been carefully tuned and tested for reliable chatbox behavior.

**Do not modify `vrchat_osc.py` or the streaming logic in `main.py` (`_process_input` → `stream_text`) without explicit permission.** Changes to:
- How text is split into chunks (144 char limit)
- Word-by-word streaming timing and behavior
- Typing indicator logic
- UDP reordering protection (final authoritative send)

...can break the chatbox experience. If you need different streaming behavior, ask first.

---

## Components

| File | Purpose |
|------|---------|
| `main.py` | Main orchestrator + entry point |
| `stt.py` | Azure Speech STT (SpeechRecognition + Azure API) |
| `llm_client.py` | LLM client (OpenAI-compatible API) |
| `tts.py` | Azure TTS with queuing and custom device routing |
| `vrchat_osc.py` | VRChat OSC ChatBox integration with character-by-character streaming |
| `config.py` | Configuration from .env |
| `web_ui/` | Flask web interface |
| `resolve_devices.py` | Helper to find WASAPI device UUIDs for TTS routing |

## Configuration Reference

### `.env` settings

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_SPEECH_KEY` | — | Azure Speech subscription key |
| `AZURE_SPEECH_REGION` | — | Azure region (e.g., `eastasia`) |
| `OPENCODE_GO_API_KEY` | — | OpenCode Go API key |
| `OPENCODE_GO_BASE_URL` | — | OpenCode Go endpoint URL |
| `OPENCODE_GO_MODEL` | — | Model name (e.g., `mimo-v2.5-pro`) |
| `VRC_CHATBOX_IP` | `127.0.0.1` | VRChat OSC IP |
| `VRC_CHATBOX_PORT` | `9000` | VRChat OSC port |
| `AUDIO_DEVICE_INDEX` | `-1` | STT input device index (`-1` = default) |
| `STT_SILENCE_THRESHOLD` | `500` | Amplitude threshold for speech detection |
| `STT_SILENCE_CUTOFF_SEC` | `2.0` | Seconds of silence before transcribing |
| `STT_CAPTURE_MODE` | `loopback` | `loopback` (desktop audio) or `microphone` |
| `TTS_OUTPUT_DEVICE_UUID` | `""` | WASAPI UUID for TTS output (empty = default speaker) |
| `TTS_PITCH` | `0` | TTS pitch adjustment in percent (e.g., `35` = +35%) |
| `SYSTEM_PROMPT` | — | Custom system prompt for the AI personality |
| `WEB_HOST` | `0.0.0.0` | Web UI host |
| `WEB_PORT` | `5000` | Web UI port |

## Tips

- **Lower latency**: Use a fast Azure voice (e.g., `en-US-JennyNeural`)
- **Better voice**: Try Azure Neural voices with SSML for prosody
- **Audio device**: Run `python main.py --list-devices` to find your device index
- **System prompt**: Customize `SYSTEM_PROMPT` in `.env` for different personalities
- **Silence detection**: Adjust `STT_SILENCE_THRESHOLD` — lower = more sensitive, higher = less
- **Test LLM**: Click "Test LLM" in the web UI to verify your LLM connection works

## Troubleshooting

- **LLM not responding**: Click "Test LLM" button. Check `OPENCODE_GO_BASE_URL` points to your running instance
- **STT not picking up speech**: Lower `STT_SILENCE_THRESHOLD` (try 200-300 for quiet rooms)
- **STT too trigger-happy**: Raise `STT_SILENCE_THRESHOLD` (try 800-1000 for noisy rooms)
- **TTS not heard in VRChat**: Set `TTS_OUTPUT_DEVICE_UUID` to a virtual cable device, route it to VRChat mic in Voicemeeter
- **TTS plays but Voicemeeter doesn't capture it**: The friendly device name may not work — use `TTS_OUTPUT_DEVICE_UUID` (WASAPI UUID) instead
- **Segfault on startup**: Do not install `comtypes`/`pycaw` in the main Python environment. Use `resolve_devices.py` as a separate one-time helper script
- **ChatBox not showing**: Verify OSC is enabled in VRChat Settings and port matches
- **Process stays running after Ctrl+C**: Use `taskkill /F /PID <pid>` or restart your computer

## License

MIT
