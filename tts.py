"""Text-to-Speech using Azure Speech SDK.

Converts text to speech and plays it back through the default audio output.
Route this output through VB-CABLE to use as VRChat microphone input.
"""

import time
import threading
import queue
from typing import Optional


class AzureTTS:
    """Azure TTS with configurable voice."""

    def __init__(
        self,
        subscription_key: str,
        region: str,
        voice: str = "en-US-AriaNeural",
        output_device: str = "",
    ):
        self.subscription_key = subscription_key
        self.region = region
        self.voice = voice
        self.output_device = output_device  # e.g., "CABLE Input" - for documentation only, Azure uses default
        self._queue = queue.Queue()
        self._running = False
        self._thread = None
        self.on_status = None
        self.on_speaking_start = None   # callback() when TTS starts playing
        self.on_speaking_end = None     # callback() when TTS finishes

    def _status(self, msg: str):
        if self.on_status:
            self.on_status(msg)

    def enqueue(self, text: str):
        """Add text to the TTS queue."""
        if text:
            self._queue.put(text)

    def start(self):
        """Start the TTS playback thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._play_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop TTS."""
        self._running = False
        # Drain queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def set_voice(self, voice: str):
        """Change TTS voice."""
        self.voice = voice

    def _play_loop(self):
        """Main loop: dequeue text, synthesize, play."""
        import azure.cognitiveservices.speech as speechsdk

        speech_config = speechsdk.SpeechConfig(
            subscription=self.subscription_key, region=self.region
        )
        speech_config.speech_synthesis_voice_name = self.voice

        # Use default speaker (set VB-CABLE as default in Windows for VRChat integration)
        audio_config = speechsdk.audio.AudioOutputConfig(
            use_default_speaker=True
        )
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config, audio_config=audio_config
        )

        self._status("TTS ready")

        while self._running:
            try:
                text = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            if not text:
                continue

            self._status(f"Speaking: {text}")
            if self.on_speaking_start:
                self.on_speaking_start()

            # Sync synthesis — plays through default output
            try:
                result = synthesizer.speak_text_async(text).get()

                if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                    self._status("Done speaking")
                elif result.reason == speechsdk.ResultReason.Canceled:
                    error_details = result.cancellation_details.error_details or "Unknown error"
                    self._status(f"TTS canceled: {error_details}")
                    print(f"TTS ERROR: {error_details}")
            except Exception as e:
                self._status(f"TTS error: {e}")
                print(f"TTS EXCEPTION: {e}")

            if self.on_speaking_end:
                self.on_speaking_end()
