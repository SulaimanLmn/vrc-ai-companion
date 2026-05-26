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
- **[VB-CABLE](https://vb-audio.com/Cable/)** — route TTS audio into VRChat mic
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

### 3. Enable OSC in VRChat

- VRChat Settings → OSC → Enable
- Default port: `9000`

### 4. Set your Windows microphone to VB-CABLE Input

- Windows Sound Settings → Recording → Set **VB-CABLE Input** as default mic
- This way TTS audio gets broadcast as your voice in VRChat

### 5. Run

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

## Components

| File | Purpose |
|------|---------|
| `main.py` | Main orchestrator + entry point |
| `stt.py` | Azure Speech STT (streaming, VAD-based) |
| `llm_client.py` | LLM client (OpenAI-compatible API) |
| `tts.py` | Azure TTS with queuing |
| `vrchat_osc.py` | VRChat OSC ChatBox integration |
| `config.py` | Configuration from .env |
| `web_ui/` | Flask web interface |

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
- **TTS not heard in VRChat**: Make sure Windows default playback device is set to VB-CABLE Input
- **ChatBox not showing**: Verify OSC is enabled in VRChat Settings and port matches

## License

MIT
