# NeuroClone — Architecture & Project Guide

> For AI models and developers: understand this project at a glance.

## Purpose

A Neuro-sama-inspired AI companion for **VRChat**. Listens to nearby conversation through VRChat's audio output, thinks via an LLM, and responds out loud through the user's microphone — while simultaneously displaying the response as typed text in VRChat's chatbox.

## The Flow (Input → Output)

```
┌─────────────────────────────────────────────────────────────────┐
│                        VRChat World                              │
│                                                                  │
│  Other Players talk ──► VRChat audio plays on user's PC          │
│                           │                                       │
└───────────────────────────┼───────────────────────────────────────┘
                            │  WASAPI Loopback Capture
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  1. STT (Azure Speech Service)                                   │
│     • sounddevice captures desktop audio output via WASAPI       │
│       loopback (hears what user hears from VRChat)               │
│     • VAD: detects speech vs silence using amplitude threshold   │
│     • When silence detected → sends audio chunk to Azure STT    │
│     • Returns transcribed text                                   │
└────────────────────────────┬─────────────────────────────────────┘
                             │  "Hey what do you think about..."
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  2. LLM (OpenCode Go → DeepSeek V4 Flash)                        │
│     • Sends transcribed text as user message                     │
│     • Includes system prompt + conversation history (last 20)    │
│     • Base URL: https://opencode.ai/zen/go/v1                   │
│     • Model: DeepSeek V4 Flash (fastest, cheapest)               │
│     • Returns AI response text                                   │
└────────────────────────────┬─────────────────────────────────────┘
                             │  "That sounds awesome! I'd totally..."
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  3. OUTPUT — Dual Channel                                        │
│     ┌─────────────────────┐  ┌─────────────────────────────┐    │
│     │ 3a. TTS (Azure)     │  │ 3b. OSC (VRChat ChatBox)    │    │
│     │ • Text queued to    │  │ • Types response with       │    │
│     │   Azure TTS         │  │   streaming characters      │    │
│     │ • Synthesized audio │  │ • /chatbox/typing indicator │    │
│     │   plays through     │  │ • /chatbox/input sends text │    │
│     │   default speaker   │  │   shown in-game to others   │    │
│     │                     │  │                             │    │
│     │ Windows default     │  │ VRChat OSC port: 9000       │    │
│     │ playback → VB-CABLE │  │                             │    │
│     │ → set as VRChat mic │  │                             │    │
│     │ → broadcasts to     │  │                             │    │
│     │   everyone in world │  │                             │    │
│     └─────────────────────┘  └─────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

## State Machine

```
OFF ──[Enable]──► ON ──[PTT toggle]──► LISTENING (PTT)
 │                  │                        │
 │                  │  speech detected        │
 │                  ▼                        ▼
 │              THINKING ◄─────── STT text received
 │                  │
 │                  ▼
 │              SPEAKING (TTS playing)
 │                  │
 │                  ▼
 │              ON (back to listening)
 │
 └──[Disable]──► OFF (stop STT + TTS)
```

## Key Configuration (.env)

| Variable | Purpose | Default |
|---|---|---|
| `AZURE_SPEECH_KEY` | Azure Speech Service key (used for both STT and TTS) | (required) |
| `AZURE_SPEECH_REGION` | Azure region | `eastasia` |
| `OPENCODE_GO_API_KEY` | LLM API key | (required) |
| `OPENCODE_GO_BASE_URL` | LLM API endpoint | `https://opencode.ai/zen/go/v1` |
| `OPENCODE_GO_MODEL` | LLM model name | `mimo-v2.5-pro` |
| `STT_CAPTURE_MODE` | `loopback` (desktop audio) or `microphone` | `loopback` |
| `STT_SILENCE_THRESHOLD` | Amplitude threshold for speech detection | `500` |
| `STT_SILENCE_CUTOFF_SEC` | Seconds of silence before transcribing | `2.0` |
| `VRC_CHATBOX_IP` | VRChat OSC target IP | `127.0.0.1` |
| `VRC_CHATBOX_PORT` | VRChat OSC port | `9000` |
| `SYSTEM_PROMPT` | AI personality prompt | Playful, witty companion |

## Components

### `main.py` — Orchestrator
- `NeuroClone` class: state machine + component wiring
- Connects STT → LLM → TTS + OSC pipeline
- Handles debouncing (won't process while already processing/speaking)
- Web UI integration (Flask + SocketIO)
- Entry point: `python main.py`

### `stt.py` — Speech-to-Text
- Uses `sounddevice` for audio capture
- **Loopback mode**: WASAPI loopback captures desktop audio (hears VRChat people)
- **Microphone mode**: captures from specific input device
- VAD: amplitude-based speech/silence detection
- Streams audio chunks to Azure Speech Service for transcription
- Fires callback when text received

### `llm_client.py` — LLM Client
- OpenAI-compatible API client (works with OpenCode Go)
- Maintains conversation history (last 20 messages)
- Supports both blocking and streaming responses
- Default: `https://opencode.ai/zen/go/v1` with `mimo-v2.5-pro`

### `tts.py` — Text-to-Speech
- Azure Speech Service TTS
- Queue-based: multiple texts can be queued
- Plays through default speaker (route to VB-CABLE for VRChat mic)
- Callbacks for speaking start/end (used for state tracking)

### `vrchat_osc.py` — VRChat Integration
- Sends messages via OSC to VRChat ChatBox API
- `/chatbox/input` — sends text message
- `/chatbox/typing` — shows typing indicator
- `stream_text()` — character-by-character typing animation

### `web_ui/` — Web Dashboard
- Flask + SocketIO for real-time updates
- Features: Enable/Disable, PTT button, Reset, Test LLM
- Manual text input (type messages without speaking)
- Live chat log with color-coded entries
- Keyboard shortcuts: Space (hold) = PTT, E = toggle enable

## External Dependencies

| Software | Purpose | URL |
|---|---|---|
| **VB-CABLE** | Route TTS audio to VRChat mic | https://vb-audio.com/Cable/ |
| **VRChat OSC** | ChatBox integration (built into VRChat) | Enable in VRChat Settings |
| **Azure Speech Service** | STT + TTS | Azure portal |

## Setup Summary (Windows)

1. `git clone` → `pip install -r requirements.txt`
2. Copy `.env.example` → `.env`, fill in keys
3. Install VB-CABLE, set Windows playback → VB-CABLE Input, set Windows recording → VB-CABLE Output as VRChat mic
4. Enable OSC in VRChat Settings
5. `python main.py` → open `http://localhost:5000`
6. Click **Test LLM** to verify connection, then **Enable**

## Available LLM Models (OpenCode Go)

- **mimo-v2.5-pro** — recommended (works with full responses, needs ~50+ tokens for short answers)
- **Qwen3.6 Plus** — very good quality, good humor (more expensive)
- **Qwen3.5 Plus** — slightly older Qwen (more expensive)
- **MiniMax M2.7** — great for roleplay/humor
- **Kimi K2.6** — good but verbose
- **DeepSeek V4 Flash** — fastest, cheapest (returns empty content)
- **DeepSeek V4 Pro** — higher quality, slower (returns empty content)
- **MiniMax M2.5** — older MiniMax (returns empty content)
- **MiMo-V2.5** — basic MiMo (returns empty content)
- **GLM-5.1** — Zhipu model (returns empty content)
- **GLM-5** — older GLM (returns empty content)

## License

MIT
