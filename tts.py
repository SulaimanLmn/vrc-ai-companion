"""Text-to-Speech using Azure Speech SDK.

Converts text to speech and plays it back through the default audio output.
Route this output through VB-CABLE to use as VRChat microphone input.

IMPORTANT: The SpeechSynthesizer is created ONCE and kept alive for the
entire app lifetime. Creating/destroying synthesizers repeatedly corrupts
the Windows audio device driver (WASAPI endpoint). Never call shutdown()
unless the app is truly exiting.
"""

import time
import threading
import queue
import xml.sax.saxutils
from typing import Optional


class AzureTTS:
    """Azure TTS with configurable voice."""

    def __init__(
        self,
        subscription_key: str,
        region: str,
        voice: str = "en-US-AshleyNeural",
        output_device_uuid: str = "",
        pitch: int = 0,
    ):
        self.subscription_key = subscription_key
        self.region = region
        self.voice = voice
        self.pitch = pitch  # pitch adjustment in percent (e.g., 35 = +35%)
        self.output_device_uuid = output_device_uuid  # WASAPI UUID for custom output device
        self._queue = queue.Queue()
        self._running = False
        self._paused = False
        self._thread = None
        self.on_status = None
        self.on_speaking_start = None   # callback() when TTS starts playing
        self.on_speaking_end = None     # callback() when TTS finishes
        self._ready = False

    def _status(self, msg: str):
        if self.on_status:
            self.on_status(msg)

    def enqueue(self, text: str, done_event: threading.Event = None):
        """Add text to the TTS queue.

        If done_event is provided, it will be set when synthesis completes
        (or fails). Used by test_tts() for synchronous waiting.
        """
        if text:
            self._queue.put((text, done_event))

    def start(self):
        """Start the TTS playback thread (idempotent — only creates once)."""
        if self._thread is not None and self._thread.is_alive():
            # Thread already running — just unpause
            self._paused = False
            print("[TTS] start() — already running, unpaused")
            return
        if self._running:
            return
        self._running = True
        self._paused = False
        self._ready = False
        self._thread = threading.Thread(target=self._play_loop, daemon=True)
        self._thread.start()
        # Wait for thread to be ready
        for i in range(50):  # 5 second timeout
            if self._ready:
                print(f"[TTS] start() — ready after {i*0.1:.1f}s")
                return
            time.sleep(0.1)
        print(f"[TTS] start() — TIMEOUT waiting for _ready (5s)!")

    def pause(self):
        """Pause TTS: drain queue but keep thread+synthesizer alive.

        Does NOT destroy the SpeechSynthesizer — that would corrupt the
        Windows audio device. The synthesizer stays alive and bound to
        the default audio endpoint.
        """
        self._paused = True
        # Drain queue
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                # Signal completion on any pending done_events
                if isinstance(item, tuple) and len(item) == 2:
                    _, done_event = item
                    if done_event:
                        done_event.set()
            except queue.Empty:
                break
        print("[TTS] pause() — queue drained, synthesizer kept alive")

    def shutdown(self):
        """Full shutdown: kill thread and release synthesizer.

        Only call this when the app is truly exiting, NOT on
        enable/disable cycles. After this you must restart the app.
        """
        print("[TTS] shutdown() — stopping thread and releasing synthesizer")
        self._running = False
        self._paused = True
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        if self._thread:
            self._thread.join(timeout=5)
            still_alive = self._thread.is_alive()
            print(f"[TTS] shutdown() — thread alive after join: {still_alive}")
            self._thread = None
        self._ready = False

    def stop(self):
        """DEPRECATED: Use pause() instead. Kept for backward compatibility."""
        self.pause()

    def set_voice(self, voice: str):
        """Change TTS voice (e.g. 'en-US-AshleyNeural')."""
        self.voice = voice

    def set_pitch(self, percent: int):
        """Adjust TTS pitch in percent (e.g. 35 = +35%, -10 = -10%)."""
        self.pitch = percent
        print(f"[TTS] Pitch set to {percent:+d}%")

    def _to_ssml(self, text: str) -> str:
        """Wrap plain text in SSML with voice and pitch adjustment."""
        escaped = xml.sax.saxutils.escape(text)
        pitch_str = f"{self.pitch:+d}%" if self.pitch != 0 else "0%"
        return (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
            f'<voice name="{self.voice}">'
            f'<prosody pitch="{pitch_str}">{escaped}</prosody>'
            f'</voice></speak>'
        )

    def _play_loop(self):
        """Main loop: dequeue text, synthesize, play."""
        import azure.cognitiveservices.speech as speechsdk

        synthesizer = None
        try:
            speech_config = speechsdk.SpeechConfig(
                subscription=self.subscription_key, region=self.region
            )
            speech_config.speech_synthesis_voice_name = self.voice

            if self.output_device_uuid and self.output_device_uuid.strip():
                # Route TTS to a specific output device by WASAPI UUID
                print(f"[TTS] Using output device UUID: {self.output_device_uuid[:50]}...")
                audio_config = speechsdk.audio.AudioOutputConfig(
                    device_name=self.output_device_uuid
                )
            else:
                audio_config = speechsdk.audio.AudioOutputConfig(
                    use_default_speaker=True
                )
            synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=speech_config, audio_config=audio_config
            )
            print("[TTS] _play_loop — synthesizer created OK (will keep alive)")

            self._ready = True
            self._status("TTS ready")
        except Exception as e:
            self._status(f"TTS init error: {e}")
            print(f"[TTS] _play_loop — INIT ERROR: {e}")
            self._ready = True
            return

        while self._running:
            try:
                item = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            # Handle (text, done_event) tuples for synchronous waiting
            if isinstance(item, tuple) and len(item) == 2:
                text, done_event = item
            else:
                text = item
                done_event = None

            if not text:
                if done_event:
                    done_event.set()
                continue

            # If paused, skip playback but signal completion
            if self._paused:
                if done_event:
                    done_event.set()
                continue

            self._status(f"Speaking: {text}")
            if self.on_speaking_start:
                self.on_speaking_start()

            try:
                ssml = self._to_ssml(text)
                result = synthesizer.speak_ssml_async(ssml).get()
                if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                    self._status("Done speaking")
                elif result.reason == speechsdk.ResultReason.Canceled:
                    error_details = result.cancellation_details.error_details or "Unknown error"
                    self._status(f"TTS canceled: {error_details}")
                    print(f"[TTS] _play_loop — CANCELED: {error_details}")
            except AttributeError:
                # Azure SDK internal cleanup noise — harmless, ignore
                pass
            except Exception as e:
                print(f"[TTS] _play_loop — note: {e}")
            finally:
                time.sleep(0.1)
                if done_event:
                    done_event.set()

            if self.on_speaking_end:
                self.on_speaking_end()

        # Thread exiting (only on app shutdown)
        # Synthesizer goes out of scope; Azure SDK cleans up via __del__
        print("[TTS] _play_loop — thread exiting (app shutdown)")
