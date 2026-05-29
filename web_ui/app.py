"""Flask web interface for Companion."""

import io
import json
import os
import sys
import threading
import time
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit


# ── Stdout capture for live debug page ──

class _DebugCapture(io.TextIOBase):
    """Redirects sys.stdout to also emit 'debug_line' via SocketIO."""

    def __init__(self, socketio):
        self.socketio = socketio
        self._original = sys.__stdout__

    def write(self, text):
        self._original.write(text)
        self._original.flush()
        if text.strip() and not self._is_http_log(text):
            try:
                self.socketio.emit("debug_line", text)
            except Exception:
                pass

    @staticmethod
    def _is_http_log(text: str) -> bool:
        """Filter out Flask/Werkzeug HTTP request logs."""
        return ('"GET' in text or '"POST' in text) and 'HTTP/1.' in text

    def flush(self):
        self._original.flush()


# ── .env helpers ──


def _load_env():
    """Read .env file into a dict (handles multi-line quoted values)."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    env_path = os.path.abspath(env_path)
    values = {}
    if not os.path.exists(env_path):
        return values
    with open(env_path, encoding="utf-8") as f:
        lines = f.readlines()
    # Merge multi-line quoted values into single lines
    merged = []
    buf = ""
    in_quote = False
    for line in lines:
        stripped = line.rstrip("\n")
        if in_quote:
            buf += "\n" + stripped
            if stripped.endswith('"'):
                in_quote = False
                merged.append(buf)
                buf = ""
            continue
        if "=" not in stripped or stripped.startswith("#"):
            merged.append(stripped)
            continue
        key, _, val = stripped.partition("=")
        # Detect start of multi-line value: value is only '"' and line ends with '='
        if val.strip() == '"' or (val.strip().startswith('"') and not stripped.rstrip().endswith('"')):
            in_quote = True
            buf = stripped
        else:
            merged.append(stripped)
    if buf:
        merged.append(buf)
    for line in merged:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"')
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
                val = updates[key]
                # Write multi-line values in quoted format
                if "\n" in val:
                    new_lines.append(f'{key}="\n{val}\n"\n')
                else:
                    new_lines.append(f"{key}={val}\n")
                updated_keys.discard(key)
                continue
        new_lines.append(line)
    for key in updated_keys:
        val = updates[key]
        if "\n" in val:
            new_lines.append(f'{key}="\n{val}\n"\n')
        else:
            new_lines.append(f"{key}={val}\n")
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
                # Append host API suffix (MME, WASAPI, etc.) to differentiate duplicates
                try:
                    host_api = pa.get_host_api_info_by_index(info["hostApi"])["name"]
                    host_api = host_api.replace("Windows ", "").replace("DirectSound", "DSound")
                except Exception:
                    host_api = ""
                marker = " [LOOPBACK]" if "loopback" in name.lower() else ""
                suffix = f" [{host_api}]" if host_api else ""
                full_name = name + marker + suffix
                if name not in seen:
                    seen.add(name)
                    devices.append({"index": i, "name": full_name})
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
    {"key": "LLM_API_KEY", "label": "LLM API Key", "type": "password", "section": "llm"},
    {"key": "LLM_BASE_URL", "label": "API Base URL", "type": "text", "section": "llm"},
    {"key": "LLM_MODEL", "label": "AI Model", "type": "llm_model", "section": "llm"},
    {"key": "LLM_MAX_TOKENS", "label": "Response Length (tokens)", "type": "range", "min": 50, "max": 5000, "step": 50, "section": "llm"},
    {"key": "LLM_MAX_HISTORY", "label": "Conversation Memory (exchanges)", "type": "range", "min": 0, "max": 50, "step": 1, "section": "llm", "note": "0 = unlimited"},
    {"key": "AUDIO_DEVICE_INDEX", "label": "Microphone / Audio Input", "type": "dropdown", "options_key": "input_devices", "section": "audio"},
    {"key": "STT_CAPTURE_MODE", "label": "Capture Mode", "type": "dropdown", "options": ["loopback", "microphone"], "section": "audio"},
    {"key": "STT_SILENCE_THRESHOLD", "label": "Mic Sensitivity", "type": "range", "min": 100, "max": 2000, "step": 50, "section": "stt"},
    {"key": "STT_SILENCE_CUTOFF_SEC", "label": "Silence Wait (seconds)", "type": "range", "min": 0.5, "max": 5.0, "step": 0.25, "section": "stt"},
    {"key": "AZURE_SPEECH_KEY", "label": "Azure Speech Key", "type": "password", "section": "stt"},
    {"key": "AZURE_SPEECH_REGION", "label": "Azure Region", "type": "dropdown", "section": "stt", "options": [
        "australiaeast","brazilsouth","canadacentral","canadaeast",
        "centralindia","centralus","eastasia","eastus","eastus2",
        "francecentral","germanywestcentral","italynorth","japaneast",
        "japanwest","koreacentral","northcentralus","northeurope",
        "norwayeast","qatarcentral","southafricanorth","southcentralus",
        "southeastasia","swedencentral","switzerlandnorth","switzerlandwest",
        "uaenorth","uksouth","ukwest","westcentralus","westeurope",
        "westus","westus2","westus3"
    ]},
    {"key": "TTS_PITCH", "label": "Voice Pitch (%)", "type": "range", "min": -50, "max": 50, "step": 5, "section": "tts"},
    {"key": "VISION_TRIGGER_PHRASE", "label": "Screen Capture Phrase", "type": "text", "section": "vision"},
    {"key": "VISION_CAPTURE_WINDOW", "label": "Window to Capture", "type": "dropdown", "options_key": "windows", "section": "vision"},
    {"key": "ACTIVATION_PHRASE", "label": "Text Filter (phrase to respond to)", "type": "text", "section": "general", "note": "Empty = respond to everything"},
    {"key": "WAKE_KEYWORD", "label": "Wake Word (Vosk)", "type": "text", "section": "general", "note": "Empty = energy VAD fallback"},
    {"key": "SYSTEM_PROMPT", "label": "Personality Prompt", "type": "textarea", "section": "general"},
]


def create_app(neuro):
    app = Flask(__name__, template_folder="templates")
    app.config["SECRET_KEY"] = "companion-secret"
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    neuro.on_status_change = lambda status: socketio.emit("status_update", status)
    neuro.on_chat_entry = lambda role, text: socketio.emit("chat_entry", {"role": role, "text": text})

    # Mic level → UI meter
    if hasattr(neuro.stt, 'on_level'):
        neuro.stt.on_level = lambda amp: socketio.emit("level", amp)

    # Stdout capture → debug page
    _debug_capture = _DebugCapture(socketio)
    sys.stdout = _debug_capture
    sys.stderr = _debug_capture

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
        # Snapshot current values before saving (for restart detection)
        before = _load_env()
        try:
            _save_env(data)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        # Apply live updates where possible
        if "STT_SILENCE_THRESHOLD" in data:
            try:
                if hasattr(neuro.stt, 'silence_threshold'):
                    neuro.stt.silence_threshold = int(data["STT_SILENCE_THRESHOLD"])
                if hasattr(neuro.stt, 'recognizer'):
                    neuro.stt.recognizer.energy_threshold = int(data["STT_SILENCE_THRESHOLD"])
            except Exception: pass
        if "STT_SILENCE_CUTOFF_SEC" in data:
            try:
                if hasattr(neuro.stt, 'silence_cutoff_sec'):
                    neuro.stt.silence_cutoff_sec = float(data["STT_SILENCE_CUTOFF_SEC"])
                if hasattr(neuro.stt, 'recognizer'):
                    neuro.stt.recognizer.pause_threshold = float(data["STT_SILENCE_CUTOFF_SEC"])
            except Exception: pass
        if "TTS_PITCH" in data:
            try: neuro.tts.set_pitch(int(data["TTS_PITCH"]))
            except Exception: pass
        if "SYSTEM_PROMPT" in data:
            try: neuro.llm.system_prompt = data["SYSTEM_PROMPT"]
            except Exception: pass
        # Only flag keys that actually changed value
        needs_restart = [
            k for k in data if k in (
                "AUDIO_DEVICE_INDEX", "STT_CAPTURE_MODE", "LLM_MODEL",
                "VISION_CAPTURE_WINDOW", "ACTIVATION_PHRASE", "VISION_TRIGGER_PHRASE",
            ) and data.get(k) != before.get(k)
        ]
        return jsonify({"ok": True, "needs_restart": needs_restart})

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

    @app.route("/api/test_wake_word", methods=["POST"])
    def api_test_wake_word():
        """Simple ping — wake word is tested by saying it and watching status."""
        return jsonify({"success": True, "message": "Say your wake word and check the status dot."})

    @app.route("/api/export_log")
    def api_export_log():
        log = neuro.get_chat_log()
        return jsonify(log)

    return app, socketio
