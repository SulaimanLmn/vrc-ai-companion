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
OPENCODE_GO_BASE_URL = os.getenv("OPENCODE_GO_BASE_URL", "http://localhost:8080/v1")
OPENCODE_GO_MODEL = os.getenv("OPENCODE_GO_MODEL", "gpt-4o-mini")

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
