"""Configuration loader from .env and defaults."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

# Azure Speech
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "eastasia")

# LLM (OpenCode Go — OpenAI-compatible)
OPENCODE_GO_API_KEY = os.getenv("OPENCODE_GO_API_KEY", "")
OPENCODE_GO_BASE_URL = os.getenv("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1")
OPENCODE_GO_MODEL = os.getenv("OPENCODE_GO_MODEL", "mimo-v2.5-pro")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "150"))
LLM_MAX_HISTORY = int(os.getenv("LLM_MAX_HISTORY", "5"))

# Vision / Screen Capture (uses the main LLM model)
VISION_TRIGGER_PHRASE = os.getenv("VISION_TRIGGER_PHRASE", "look at this").lower()
VISION_CAPTURE_WINDOW = os.getenv("VISION_CAPTURE_WINDOW", "VRChat")

# VRChat OSC
VRC_CHATBOX_IP = os.getenv("VRC_CHATBOX_IP", "127.0.0.1")
VRC_CHATBOX_PORT = int(os.getenv("VRC_CHATBOX_PORT", "9000"))

# Audio
AUDIO_DEVICE_INDEX = int(os.getenv("AUDIO_DEVICE_INDEX", "-1"))

# System prompt
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a playful, witty AI companion. Be fun, slightly chaotic, but kind. "
    "Keep responses short (1-3 sentences). You're chatting with people in VRChat.",
)

# Web UI
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "5000"))

# STT tuning
STT_SILENCE_THRESHOLD = int(os.getenv("STT_SILENCE_THRESHOLD", "500"))
STT_SILENCE_CUTOFF_SEC = float(os.getenv("STT_SILENCE_CUTOFF_SEC", "2.0"))
STT_CAPTURE_MODE = os.getenv("STT_CAPTURE_MODE", "loopback")  # "loopback" or "microphone"

# TTS output device (empty = default speaker, or specify device name/ID)
TTS_OUTPUT_DEVICE = os.getenv("TTS_OUTPUT_DEVICE", "")

# TTS pitch adjustment in percent (e.g. 35 = +35%, -10 = -10%)
TTS_PITCH = int(os.getenv("TTS_PITCH", "0"))

# Device UUID for TTS output (empty = default speaker)
# Obtain via: python resolve_devices.py <pyaudio_index>
TTS_OUTPUT_DEVICE_UUID = os.getenv("TTS_OUTPUT_DEVICE_UUID", "")
