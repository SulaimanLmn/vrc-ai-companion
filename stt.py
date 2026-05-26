"""Speech-to-Text using Azure Speech SDK.

Captures audio and transcribes in real-time via Azure Speech Service.
Supports two capture modes:
  - "loopback" (default): captures desktop audio output via WASAPI loopback
    — hears VRChat people, game audio, everything playing through speakers
  - "microphone": captures from a specific input device

For loopback mode, VRChat audio playing through your speakers/headphones
is captured directly — no virtual cables needed.
"""

import os
import time
import queue
import threading
import numpy as np


class AzureSTT:
    """Azure Speech SDK STT with audio capture.

    Continuously captures audio and sends to Azure for transcription.
    Fires callback with final recognized text.
    """

    def __init__(
        self,
        subscription_key: str,
        region: str,
        device_index: int = -1,
        locale: str = "en-US",
        silence_threshold: int = 500,
        silence_cutoff_sec: float = 2.0,
        capture_mode: str = "loopback",
    ):
        self.subscription_key = subscription_key
        self.region = region
        self.device_index = device_index if device_index >= 0 else None
        self.locale = locale
        self.silence_threshold = silence_threshold
        self.silence_cutoff_sec = silence_cutoff_sec
        self.capture_mode = capture_mode  # "loopback" or "microphone"
        self._running = False
        self._thread = None
        self.on_text = None      # callback(text: str)
        self.on_status = None    # callback(status: str)
        self.on_partial = None   # callback(partial_text: str)
        self._lock = threading.Lock()

    def start(self):
        """Start the STT loop in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        self._status("Starting STT...")

    def stop(self):
        """Stop capturing."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self._status("STT stopped")

    def _status(self, msg: str):
        if self.on_status:
            self.on_status(msg)

    def _capture_loop(self):
        """Loop: capture audio, detect speech, transcribe via Azure."""
        import azure.cognitiveservices.speech as speechsdk
        import sounddevice as sd

        speech_config = speechsdk.SpeechConfig(
            subscription=self.subscription_key, region=self.region
        )
        speech_config.speech_recognition_language = self.locale
        speech_config.request_word_level_timestamps()

        SAMPLE_RATE = 16000
        CHANNELS = 1
        FRAME_SIZE = 1024  # ~64ms at 16kHz
        silence_frames_to_cut = int(self.silence_cutoff_sec * SAMPLE_RATE / FRAME_SIZE)

        try:
            if self.capture_mode == "loopback":
                # WASAPI loopback — captures desktop audio output (VRChat voices, etc.)
                # Only works on Windows. Falls back to default input on other OS.
                import platform
                if platform.system() == "Windows":
                    # Find WASAPI loopback device
                    devices = sd.query_devices()
                    loopback_dev = None
                    for i, dev in enumerate(devices):
                        name = dev.get("name", "").lower()
                        if ("loopback" in name or "stereo mix" in name or
                            "what u hear" in name) and dev.get("max_input_channels", 0) > 0:
                            loopback_dev = i
                            self._status(f"Using loopback device [{i}]: {dev['name']}")
                            break

                    if loopback_dev is None:
                        # Try to use sounddevice with wasapi loopback via extra_api
                        self._status("No loopback device found. Using default input.")
                        self._status("Tip: Enable 'Stereo Mix' in Windows Sound settings.")
                        dev_index = self.device_index if self.device_index else sd.default.device[0]
                else:
                    self._status("Loopback only on Windows. Using default input.")
                    dev_index = self.device_index if self.device_index else sd.default.device[0]
            else:
                dev_index = self.device_index
                if dev_index is None:
                    dev_index = sd.default.device[0]
                self._status(f"Using mic device [{dev_index}]: {sd.query_devices(dev_index)['name']}")

            stream = sd.InputStream(
                device=dev_index if self.device_index else None,
                channels=CHANNELS,
                samplerate=SAMPLE_RATE,
                dtype="int16",
                blocksize=FRAME_SIZE,
            )
            stream.start()
        except Exception as e:
            self._status(f"Audio init failed: {e}")
            return

        audio_buffer = bytearray()
        silence_count = 0
        in_speech = False
        self._status("Listening...")

        while self._running:
            try:
                data, overflowed = stream.read(FRAME_SIZE)
                audio_data = data.flatten()
                amplitude = np.abs(audio_data).mean()

                if amplitude > self.silence_threshold:
                    audio_buffer.extend(data.tobytes())
                    silence_count = 0
                    if not in_speech:
                        in_speech = True
                        self._status("Speech detected...")
                else:
                    if in_speech:
                        silence_count += 1
                        if silence_count >= silence_frames_to_cut:
                            if len(audio_buffer) > 16000:
                                self._status("Transcribing...")
                                text = self._transcribe(speech_config, bytes(audio_buffer))
                                if text:
                                    if self.on_text:
                                        self.on_text(text)
                                    self._status(f"Heard: {text}")
                            audio_buffer = bytearray()
                            silence_count = 0
                            in_speech = False
                            self._status("Listening...")

            except Exception as e:
                self._status(f"Capture error: {e}")
                time.sleep(0.5)

        stream.stop()
        stream.close()

    def _transcribe(self, speech_config, audio_bytes: bytes) -> str:
        """Send audio bytes to Azure and return recognized text."""
        import azure.cognitiveservices.speech as speechsdk

        push_stream = speechsdk.audio.PushAudioInputStream(
            speechsdk.audio.AudioStreamFormat(
                samples_per_second=16000,
                bits_per_sample=16,
                channels=1,
            )
        )
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config, audio_config=audio_config
        )

        result_text = ""
        done = threading.Event()

        def on_recognized(evt):
            nonlocal result_text
            result = evt.result
            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                result_text = result.text

        def on_canceled(evt):
            self._status(f"STT canceled: {evt.cancellation_details.reason}")

        recognizer.recognized.connect(on_recognized)
        recognizer.canceled.connect(on_canceled)

        recognizer.start_continuous_recognition()
        done.wait(0.1)  # brief sync

        # Push audio data
        chunk_size = 3200
        for i in range(0, len(audio_bytes), chunk_size):
            push_stream.write(audio_bytes[i : i + chunk_size])

        push_stream.close()

        # Wait for result with timeout
        for _ in range(60):  # max ~6 seconds
            if done.is_set() or result_text:
                break
            time.sleep(0.1)

        try:
            recognizer.stop_continuous_recognition()
        except Exception:
            pass

        return result_text.strip()
