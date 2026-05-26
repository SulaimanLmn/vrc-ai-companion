"""Main orchestrator — ties STT, LLM, TTS, and VRChat OSC together.

State machine:
  OFF -> ON -> PTT_ACTIVE -> processing -> TTS speaking -> back to ON

Usage:
  python main.py              # start with web UI
  python main.py --list-devices  # list audio input devices
"""

import argparse
import threading
import time
import sys
import json

from config import (
    AZURE_SPEECH_KEY,
    AZURE_SPEECH_REGION,
    OPENCODE_GO_API_KEY,
    OPENCODE_GO_BASE_URL,
    OPENCODE_GO_MODEL,
    VRC_CHATBOX_IP,
    VRC_CHATBOX_PORT,
    AUDIO_DEVICE_INDEX,
    SYSTEM_PROMPT,
    WEB_HOST,
    WEB_PORT,
    STT_SILENCE_THRESHOLD,
    STT_SILENCE_CUTOFF_SEC,
    STT_CAPTURE_MODE,
)
from stt import AzureSTT
from llm_client import LLMClient
from tts import AzureTTS
from vrchat_osc import ChatBox


class NeuroClone:
    """Core state machine and component wiring."""

    def __init__(self):
        # State
        self.enabled = False        # Master on/off
        self.ptt_active = False     # Push-to-talk active
        self.is_processing = False  # LLM thinking
        self.is_speaking = False    # TTS playing

        # Components
        self.stt = AzureSTT(
            subscription_key=AZURE_SPEECH_KEY,
            region=AZURE_SPEECH_REGION,
            device_index=AUDIO_DEVICE_INDEX,
            silence_threshold=STT_SILENCE_THRESHOLD,
            silence_cutoff_sec=STT_SILENCE_CUTOFF_SEC,
            capture_mode=STT_CAPTURE_MODE,
        )
        self.stt.on_status = self._on_stt_status

        self.llm = LLMClient(
            api_key=OPENCODE_GO_API_KEY,
            base_url=OPENCODE_GO_BASE_URL,
            model=OPENCODE_GO_MODEL,
            system_prompt=SYSTEM_PROMPT,
        )
        self.llm.on_status = self._on_llm_status

        self.tts = AzureTTS(
            subscription_key=AZURE_SPEECH_KEY,
            region=AZURE_SPEECH_REGION,
        )
        self.tts.on_status = self._on_tts_status
        self.tts.on_speaking_start = lambda: setattr(self, "is_speaking", True) or self._broadcast_status()
        self.tts.on_speaking_end = lambda: setattr(self, "is_speaking", False) or self._broadcast_status()

        self.chatbox = ChatBox(ip=VRC_CHATBOX_IP, port=VRC_CHATBOX_PORT)

        # UI callbacks (set by web_ui)
        self.on_status_change = None   # callback(status_dict)
        self.on_chat_entry = None      # callback(role, text)

        # Conversation log
        self.chat_log = []
        self._log_lock = threading.Lock()

        # Wire STT -> process
        self.stt.on_text = self._on_text_received

    # -- State transitions --

    def toggle_enabled(self):
        """Toggle master on/off."""
        self.enabled = not self.enabled
        if self.enabled:
            self.stt.start()
            self.tts.start()
            self._log("system", "NeuroClone enabled")
        else:
            self.ptt_active = False
            self.stt.stop()
            self.tts.stop()
            self._log("system", "NeuroClone disabled")
        self._broadcast_status()

    def toggle_ptt(self):
        """Toggle push-to-talk."""
        if not self.enabled:
            return
        self.ptt_active = not self.ptt_active
        label = "PTT ON" if self.ptt_active else "PTT OFF"
        self._log("system", label)
        self._broadcast_status()

    def reset_conversation(self):
        """Clear chat history."""
        self.llm.reset_history()
        with self._log_lock:
            self.chat_log.clear()
        self._log("system", "Conversation reset")
        self._broadcast_status()

    def send_text(self, text: str):
        """Manually send text (from web UI chat input)."""
        if not text.strip():
            return
        self._process_input(text)

    def test_llm(self) -> dict:
        """Test LLM connection and return result."""
        try:
            reply = self.llm.chat("Say 'connection test OK' in 3 words", max_tokens=15)
            if reply:
                return {"success": True, "reply": reply}
            else:
                return {"success": False, "error": "Empty response from LLM"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -- Core pipeline --

    def _on_text_received(self, text: str):
        """STT callback: received transcribed speech."""
        if not self.enabled:
            return
        # In PTT mode, only respond when PTT is active
        if self.ptt_active or True:  # always process when enabled; PTT gates STT start/stop
            self._process_input(text)

    def _process_input(self, text: str):
        """Full pipeline: input -> LLM -> TTS -> VRChat."""
        if self.is_processing or self.is_speaking:
            return  # debounce

        self.is_processing = True
        self._log("user", text)
        self._broadcast_status()

        # Send to VRChat chatbox what was heard
        self.chatbox.send_message(f"[heard] {text}", visible=True)

        # LLM response
        try:
            reply = self.llm.chat(text)
        except Exception as e:
            reply = ""
            self._log("system", f"LLM error: {e}")

        self.is_processing = False
        self._broadcast_status()

        if reply:
            self._log("assistant", reply)

            # VRChat: stream typing + text
            self.chatbox.start_typing()
            time.sleep(0.3)
            self.chatbox.stream_text(reply, delay=0.03)

            # TTS
            self.tts.enqueue(reply)
        else:
            self._log("system", "No LLM response received")

        self._broadcast_status()

    # -- Status callbacks --

    def _on_stt_status(self, msg: str):
        self._broadcast_status()

    def _on_llm_status(self, msg: str):
        self._broadcast_status()

    def _on_tts_status(self, msg: str):
        self._broadcast_status()

    def _log(self, role: str, text: str):
        entry = {"role": role, "text": text, "time": time.time()}
        with self._log_lock:
            self.chat_log.append(entry)
            # Keep last 200 entries
            if len(self.chat_log) > 200:
                self.chat_log = self.chat_log[-200:]
        if self.on_chat_entry:
            self.on_chat_entry(role, text)

    def _broadcast_status(self):
        """Send status dict to UI callback."""
        status = {
            "enabled": self.enabled,
            "ptt_active": self.ptt_active,
            "is_processing": self.is_processing,
            "is_speaking": self.is_speaking,
            "state": self._state_label(),
        }
        if self.on_status_change:
            self.on_status_change(status)

    def _state_label(self) -> str:
        if not self.enabled:
            return "OFF"
        if self.is_speaking:
            return "SPEAKING"
        if self.is_processing:
            return "THINKING"
        if self.ptt_active:
            return "LISTENING (PTT)"
        return "ON"

    def get_chat_log(self) -> list:
        with self._log_lock:
            return list(self.chat_log)

    def get_status(self) -> dict:
        return {
            "enabled": self.enabled,
            "ptt_active": self.ptt_active,
            "is_processing": self.is_processing,
            "is_speaking": self.is_speaking,
            "state": self._state_label(),
            "chat_log_count": len(self.chat_log),
        }


def main():
    parser = argparse.ArgumentParser(description="NeuroClone — AI VTuber for VRChat")
    parser.add_argument("--list-devices", action="store_true", help="List audio input devices")
    args = parser.parse_args()

    if args.list_devices:
        from stt import AzureSTT
        # Just list devices via pyaudio
        import pyaudio
        p = pyaudio.PyAudio()
        info = p.get_host_api_info_by_index(0)
        print("Available audio input devices:")
        for i in range(info.get("deviceCount")):
            dev = p.get_device_info_by_host_api_device_index(0, i)
            if dev.get("maxInputChannels") > 0:
                print(f"  [{i}] {dev['name']}")
        p.terminate()
        return

    # Check config
    if not AZURE_SPEECH_KEY:
        print("ERROR: AZURE_SPEECH_KEY not set. Copy .env.example to .env and fill in your keys.")
        sys.exit(1)
    if not OPENCODE_GO_API_KEY:
        print("ERROR: OPENCODE_GO_API_KEY not set. Copy .env.example to .env and fill in your keys.")
        sys.exit(1)

    neuro = NeuroClone()

    # Start web UI
    from web_ui.app import create_app
    app = create_app(neuro)
    print(f"\n{'='*50}")
    print(f"  NeuroClone — AI VTuber for VRChat")
    print(f"{'='*50}")
    print(f"\n  Web UI: http://localhost:{WEB_PORT}")
    print(f"  Press Ctrl+C to stop\n")

    try:
        app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        neuro.toggle_enabled()  # cleanup if running
        print("\nGoodbye!")


if __name__ == "__main__":
    main()
