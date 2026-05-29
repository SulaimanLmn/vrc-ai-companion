"""Flask web interface for NeuroClone."""

import json
import os
import threading
import time
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit


def _load_env():
    """Read .env file into a dict."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    env_path = os.path.abspath(env_path)
    values = {}
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
    return values


def _save_env(updates: dict):
    """Update specific keys in .env, preserving comments and order."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    env_path = os.path.abspath(env_path)
    if not os.path.exists(env_path):
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("# .env\n")
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    updated_keys = set(updates.keys())
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in updated_keys:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.discard(key)
                continue
        new_lines.append(line)
    for key in updated_keys:
        new_lines.append(f"{key}={updates[key]}\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def _list_input_devices():
    """Return list of {index, name} for audio input devices."""
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        seen, devices = set(), []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                name = info["name"]
                marker = " [LOOPBACK]" if "loopback" in name.lower() else ""
                if name not in seen:
                    seen.add(name)
                    devices.append({"index": i, "name": name + marker})
        pa.terminate()
        return devices
    except Exception:
        return []


def _list_windows():
    """Return list of visible window titles."""
    try:
        from vision import list_window_titles
        return list_window_titles()
    except Exception:
        return []


CONFIG_UI = [
    {"key": "OPENCODE_GO_MODEL", "label": "AI Model", "type": "text", "section": "llm"},
    {"key": "LLM_MAX_TOKENS", "label": "Response Length (tokens)", "type": "range", "min": 50, "max": 1000, "step": 50, "section": "llm"},
    {"key": "LLM_MAX_HISTORY", "label": "Conversation Memory (exchanges)", "type": "range", "min": 0, "max": 50, "step": 1, "section": "llm", "note": "0 = unlimited"},
    {"key": "AUDIO_DEVICE_INDEX", "label": "Microphone / Audio Input", "type": "dropdown", "options_key": "input_devices", "section": "audio"},
    {"key": "STT_CAPTURE_MODE", "label": "Capture Mode", "type": "dropdown", "options": ["loopback", "microphone"], "section": "audio"},
    {"key": "STT_SILENCE_THRESHOLD", "label": "Mic Sensitivity", "type": "range", "min": 100, "max": 2000, "step": 50, "section": "stt"},
    {"key": "STT_SILENCE_CUTOFF_SEC", "label": "Silence Wait (seconds)", "type": "range", "min": 0.5, "max": 5.0, "step": 0.25, "section": "stt"},
    {"key": "TTS_PITCH", "label": "Voice Pitch (%)", "type": "range", "min": -50, "max": 50, "step": 5, "section": "tts"},
    {"key": "ACTIVATION_PHRASE", "label": "Text Filter (phrase to respond to)", "type": "text", "section": "general", "note": "Empty = respond to everything"},
    {"key": "VISION_TRIGGER_PHRASE", "label": "Screen Capture Phrase", "type": "text", "section": "vision"},
    {"key": "VISION_CAPTURE_WINDOW", "label": "Window to Capture", "type": "dropdown", "options_key": "windows", "section": "vision"},
    {"key": "SYSTEM_PROMPT", "label": "Personality Prompt", "type": "textarea", "section": "general"},
]


def create_app(neuro):
    app = Flask(__name__, template_folder="templates")
    app.config["SECRET_KEY"] = "neuro-clone-secret"
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    neuro.on_status_change = lambda status: socketio.emit("status_update", status)
    neuro.on_chat_entry = lambda role, text: socketio.emit("chat_entry", {"role": role, "text": text})

    # ── Config API ──

    @app.route("/api/config")
    def api_config():
        env = _load_env()
        return jsonify({
            "settings": env,
            "ui": CONFIG_UI,
            "input_devices": _list_input_devices(),
            "windows": _list_windows(),
        })

    @app.route("/api/config", methods=["POST"])
    def api_save_config():
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "No data"}), 400
        try:
            _save_env(data)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        # Apply live updates where possible
        if "STT_SILENCE_THRESHOLD" in data:
            try: neuro.stt.recognizer.energy_threshold = int(data["STT_SILENCE_THRESHOLD"])
            except Exception: pass
        if "STT_SILENCE_CUTOFF_SEC" in data:
            try: neuro.stt.recognizer.pause_threshold = float(data["STT_SILENCE_CUTOFF_SEC"])
            except Exception: pass
        if "TTS_PITCH" in data:
            try: neuro.tts.set_pitch(int(data["TTS_PITCH"]))
            except Exception: pass
        if "SYSTEM_PROMPT" in data:
            try: neuro.llm.system_prompt = data["SYSTEM_PROMPT"]
            except Exception: pass
        return jsonify({"ok": True, "needs_restart": [
            k for k in data if k in (
                "AUDIO_DEVICE_INDEX", "STT_CAPTURE_MODE", "OPENCODE_GO_MODEL",
                "VISION_CAPTURE_WINDOW", "ACTIVATION_PHRASE", "VISION_TRIGGER_PHRASE",
            )
        ]})

    # ── Existing endpoints ──

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status")
    def api_status():
        return jsonify(neuro.get_status())

    @app.route("/api/chat_log")
    def api_chat_log():
        return jsonify(neuro.get_chat_log())

    @app.route("/api/toggle_enabled", methods=["POST"])
    def api_toggle_enabled():
        neuro.toggle_enabled()
        return jsonify(neuro.get_status())

    @app.route("/api/toggle_ptt", methods=["POST"])
    def api_toggle_ptt():
        neuro.toggle_ptt()
        return jsonify(neuro.get_status())

    @app.route("/api/send_text", methods=["POST"])
    def api_send_text():
        data = request.get_json()
        text = data.get("text", "")
        neuro.send_text(text)
        return jsonify({"ok": True})

    @app.route("/api/reset", methods=["POST"])
    def api_reset():
        neuro.reset_conversation()
        return jsonify({"ok": True})

    @app.route("/api/test_llm", methods=["POST"])
    def api_test_llm():
        return jsonify(neuro.test_llm())

    @app.route("/api/test_stt", methods=["POST"])
    def api_test_stt():
        return jsonify(neuro.test_stt())

    @app.route("/api/test_tts", methods=["POST"])
    def api_test_tts():
        return jsonify(neuro.test_tts())

    return app, socketio
