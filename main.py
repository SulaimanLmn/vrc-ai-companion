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
import signal
import os

from config import (
    AZURE_SPEECH_KEY,
    AZURE_SPEECH_REGION,
    OPENCODE_GO_API_KEY,
    OPENCODE_GO_BASE_URL,
    OPENCODE_GO_MODEL,
    LLM_MAX_TOKENS,
    LLM_MAX_HISTORY,
    VISION_TRIGGER_PHRASE,
    VISION_CAPTURE_WINDOW,
    VRC_CHATBOX_IP,
    VRC_CHATBOX_PORT,
    AUDIO_DEVICE_INDEX,
    SYSTEM_PROMPT,
    WEB_HOST,
    WEB_PORT,
    STT_SILENCE_THRESHOLD,
    STT_SILENCE_CUTOFF_SEC,
    STT_CAPTURE_MODE,
    TTS_OUTPUT_DEVICE_UUID,
    TTS_PITCH,
    WAKE_KEYWORD,
)
from stt import AzureSTT, WakeWordSTT, latency_mark, reset_latency
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
        self.is_streaming = False   # VRChat chatbox streaming active

        # Components — STT
        if WAKE_KEYWORD:
            self.stt = WakeWordSTT(
                subscription_key=AZURE_SPEECH_KEY,
                region=AZURE_SPEECH_REGION,
                device_index=AUDIO_DEVICE_INDEX,
                wake_keyword=WAKE_KEYWORD,
                silence_threshold=STT_SILENCE_THRESHOLD,
                silence_cutoff_sec=STT_SILENCE_CUTOFF_SEC,
            )
            print("[WAKE] Wake word STT enabled — keyword: '{}'".format(WAKE_KEYWORD))
        else:
            self.stt = AzureSTT(
                subscription_key=AZURE_SPEECH_KEY,
                region=AZURE_SPEECH_REGION,
                device_index=AUDIO_DEVICE_INDEX,
                silence_threshold=STT_SILENCE_THRESHOLD,
                silence_cutoff_sec=STT_SILENCE_CUTOFF_SEC,
                capture_mode=STT_CAPTURE_MODE,
            )
            print("[WAKE] Wake word disabled — using legacy STT (mode: {})".format(STT_CAPTURE_MODE))
        self.stt.on_status = self._on_stt_status

        self.llm = LLMClient(
            api_key=OPENCODE_GO_API_KEY,
            base_url=OPENCODE_GO_BASE_URL,
            model=OPENCODE_GO_MODEL,
            system_prompt=SYSTEM_PROMPT,
            max_tokens=LLM_MAX_TOKENS,
            max_history=LLM_MAX_HISTORY,
        )
        self.llm.on_status = self._on_llm_status

        self.tts = AzureTTS(
            subscription_key=AZURE_SPEECH_KEY,
            region=AZURE_SPEECH_REGION,
            output_device_uuid=TTS_OUTPUT_DEVICE_UUID,
            pitch=TTS_PITCH,
        )
        self.tts.on_status = self._on_tts_status
        def _on_speaking_start():
            latency_mark("TTS start")
            self.is_speaking = True
            self.stt.pause()
            self._broadcast_status()

        def _on_speaking_end():
            latency_mark("TTS end")
            self.is_speaking = False
            self.stt.resume()
            reset_latency()  # ready for next interaction
            self._broadcast_status()

        self.tts.on_speaking_start = _on_speaking_start
        self.tts.on_speaking_end = _on_speaking_end

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
            self.tts.start()      # Creates TTS thread once; subsequent calls just unpause
            self._log("system", "NeuroClone enabled")
        else:
            self.ptt_active = False
            self.stt.stop()
            self.tts.pause()      # Drains queue but keeps synthesizer alive
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
            reply = self.llm.chat("Say 'connection test OK' in 3 words", max_tokens=100)
            if reply:
                return {"success": True, "reply": reply}
            else:
                return {"success": False, "error": "Empty response from LLM"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def test_stt(self) -> dict:
        """Test STT by transcribing an audio file from audio-test folder."""
        import os
        from pathlib import Path
        import azure.cognitiveservices.speech as speechsdk

        test_dir = Path(__file__).parent / "audio-test"
        
        if not test_dir.exists():
            return {"success": False, "error": "audio-test folder not found"}
        
        # Find first audio file
        audio_files = list(test_dir.glob("*.wav")) + list(test_dir.glob("*.mp3")) + list(test_dir.glob("*.ogg"))
        if not audio_files:
            return {"success": False, "error": "No .wav, .mp3, or .ogg files in audio-test folder"}

        try:
            speech_config = speechsdk.SpeechConfig(
                subscription=self.stt.subscription_key, region=self.stt.region
            )
            speech_config.speech_recognition_language = "en-US"

            audio_config = speechsdk.audio.AudioConfig(filename=str(audio_files[0]))
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=speech_config, audio_config=audio_config
            )

            result_text = ""
            done = threading.Event()

            def on_recognized(evt):
                nonlocal result_text
                if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                    result_text = evt.result.text

            recognizer.recognized.connect(on_recognized)
            recognizer.start_continuous_recognition()
            
            # Wait for recognition with timeout
            for _ in range(30):  # 3 second timeout
                if result_text:
                    break
                time.sleep(0.1)

            recognizer.stop_continuous_recognition()
            
            if result_text:
                return {"success": True, "message": f"Transcribed: '{result_text}' (from {audio_files[0].name})"}
            else:
                return {"success": False, "error": "No speech detected in audio file"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def test_tts(self) -> dict:
        """Test TTS by speaking a short phrase through the main TTS pipeline.

        Uses the existing synthesizer (never creates a temporary one) to
        avoid corrupting the Windows audio device.
        """
        if not self.tts:
            return {"success": False, "error": "TTS not initialized"}

        test_phrase = "Testing text to speech. Can you hear this?"

        try:
            # Queue through the main TTS pipeline with a completion event
            done_event = threading.Event()
            self.tts.enqueue(test_phrase, done_event=done_event)

            # Wait up to 15 seconds for TTS to complete
            completed = done_event.wait(timeout=15)

            if completed:
                return {"success": True, "message": "TTS test completed — check your speakers for audio"}
            else:
                return {"success": True, "message": "TTS test sent (playback may take a moment)"}

        except Exception as e:
            return {"success": True, "message": "TTS test queued (check speakers)"}

    # -- Core pipeline --

    def _on_text_received(self, text: str):
        """STT callback: received transcribed speech."""
        if not self.enabled:
            return
        latency_mark("text received")
        if self.ptt_active or True:  # always process when enabled; PTT gates STT start/stop
            self._process_input(text)

    def _process_input(self, text: str):
        """Full pipeline: input -> LLM -> TTS -> VRChat."""
        if self.is_processing or self.is_speaking or self.is_streaming:
            return  # debounce

        latency_mark("pipeline start")

        self.is_processing = True
        self._log("user", text)
        self._broadcast_status()

        # LLM response — or vision if triggered
        reply = ""
        is_vision = False
        if VISION_TRIGGER_PHRASE and VISION_TRIGGER_PHRASE in text.lower():
            is_vision = True
            self._log("system", "Capturing screen...")
            self._broadcast_status()
            try:
                from vision import _capture_window
                img = _capture_window(VISION_CAPTURE_WINDOW)
                if img:
                    reply = self.llm.chat_with_image(text, img)
                else:
                    reply = "[Could not capture screen]"
            except Exception as e:
                self._log("system", f"Vision error: {e}")
                reply = ""

        if not reply:
            try:
                reply = self.llm.chat(text)
            except Exception as e:
                reply = ""
                self._log("system", f"LLM error: {e}")

        latency_mark("LLM done")

        self.is_processing = False
        self._broadcast_status()

        if reply:
            self._log("assistant", reply)

            # Pause STT before TTS plays (saves Azure credits)
            self.stt.pause()

            # Stream text to VRChat chatbox word-by-word (parallel with TTS)
            self.is_streaming = True
            def stream():
                try:
                    self.chatbox.stream_text(reply)
                finally:
                    self.is_streaming = False
                    self._broadcast_status()
            threading.Thread(target=stream, daemon=True).start()

            # TTS plays simultaneously
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
    parser = argparse.ArgumentParser(description="AI Companion for VRChat")
    parser.add_argument("--list-devices", action="store_true", help="List audio input and output devices")
    parser.add_argument("--list-windows", action="store_true", help="List visible window titles (for VISION_CAPTURE_WINDOW)")
    args = parser.parse_args()

    if args.list_devices:
        import pyaudio
        pa = pyaudio.PyAudio()
        print("=== AUDIO OUTPUT DEVICES (for TTS) ===")
        seen_out = set()
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxOutputChannels", 0) > 0:
                name = info["name"]
                if name not in seen_out:
                    seen_out.add(name)
                    print(f'  [{i}] {name}')
        print()
        print("=== AUDIO INPUT DEVICES (for STT) ===")
        seen_in = set()
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                name = info["name"]
                marker = " [LOOPBACK]" if "loopback" in name.lower() else ""
                if name not in seen_in:
                    seen_in.add(name)
                    print(f'  [{i}] {name}{marker}')
        pa.terminate()
        print()
        print("Set AUDIO_DEVICE_INDEX in .env for STT input.")
        print("TTS routes via TTS_OUTPUT_DEVICE_UUID (run: python resolve_devices.py <index>)")
        return

    if args.list_windows:
        try:
            from vision import list_window_titles
            titles = list_window_titles()
            print("Visible window titles:")
            for t in titles:
                print(f"  {t}")
            print("\nSet VISION_CAPTURE_WINDOW in .env to one of the above.")
        except Exception as e:
            print(f"Error listing windows: {e}")
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
    app, socketio = create_app(neuro)
    print(f"\n{'='*50}")
    print(f"  NeuroClone — AI VTuber for VRChat")
    print(f"{'='*50}")
    print(f"\n  Web UI: http://localhost:{WEB_PORT}")
    print(f"  Press Ctrl+C to stop\n")

    # Ensure clean shutdown on Ctrl+C / kill
    def cleanup(*args):
        neuro.tts.shutdown()
        neuro.stt.stop()
        print("\nGoodbye!")
        os._exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        socketio.run(app, host=WEB_HOST, port=WEB_PORT, debug=False, allow_unsafe_werkzeug=True)
    except (KeyboardInterrupt, SystemExit):
        cleanup()


if __name__ == "__main__":
    main()
