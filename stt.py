"""Speech-to-Text using Azure Speech SDK.

Captures audio and transcribes in real-time via Azure Speech Service.
Supports two capture modes:
  - "loopback" (default): captures desktop audio output via WASAPI loopback
    — hears VRChat people, game audio, everything playing through speakers
  - "microphone": captures from a specific input device

For loopback mode on Windows, uses PyAudio with WASAPI host API to
capture "Stereo Mix" or a WASAPI loopback device.
"""

import os
import time
import queue
import threading
import numpy as np
import pyaudio


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

    @staticmethod
    def list_devices():
        """List available audio input devices, highlighting loopback/WASAPI devices."""
        pa = pyaudio.PyAudio()
        try:
            # Check for WASAPI host API
            wasapi_host = None
            for i in range(pa.get_host_api_count()):
                info = pa.get_host_api_info_by_index(i)
                if "WASAPI" in info.get("name", "").upper():
                    wasapi_host = i
                    break

            devices = []
            target_host = wasapi_host if wasapi_host is not None else 0

            host_info = pa.get_host_api_info_by_index(target_host)
            num_devices = host_info.get("deviceCount")
            for i in range(num_devices):
                dev = pa.get_device_info_by_host_api_device_index(target_host, i)
                if dev.get("maxInputChannels", 0) > 0:
                    name = dev.get("name", "")
                    is_loopback = any(k in name.lower() for k in [
                        "loopback", "stereo mix", "what u hear", "wasapi loopback"
                    ])
                    marker = " [LOOPBACK]" if is_loopback else ""
                    devices.append((dev["index"], name + marker))

            return devices
        finally:
            pa.terminate()

    def _capture_loop(self):
        """Loop: capture audio, detect speech, transcribe via Azure."""
        import azure.cognitiveservices.speech as speechsdk

        speech_config = speechsdk.SpeechConfig(
            subscription=self.subscription_key, region=self.region
        )
        speech_config.speech_recognition_language = self.locale
        speech_config.request_word_level_timestamps()

        SAMPLE_RATE = 16000
        CHANNELS = 1
        FRAME_SIZE = 1024  # ~64ms at 16kHz
        silence_frames_to_cut = int(self.silence_cutoff_sec * SAMPLE_RATE / FRAME_SIZE)

        pa = pyaudio.PyAudio()
        stream = None

        try:
            if self.capture_mode == "loopback":
                # Find WASAPI loopback device
                dev_index = self._find_loopback_device(pa)
                if dev_index is None:
                    # Fallback: try default input
                    default = pa.get_default_input_device_info()
                    dev_index = default["index"]
                    self._status("No loopback device found. Using default input.")
                    self._status("Tip: Enable 'Stereo Mix' in Windows Sound settings → Recording tab.")
                else:
                    self._status(f"Using loopback device [{dev_index}]: {pa.get_device_info_by_index(dev_index)['name']}")
            else:
                dev_index = self.device_index
                if dev_index is None:
                    dev_index = pa.get_default_input_device_info()["index"]
                self._status(f"Using mic device [{dev_index}]: {pa.get_device_info_by_index(dev_index)['name']}")

            stream = pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=dev_index,
                frames_per_buffer=FRAME_SIZE,
            )
        except Exception as e:
            self._status(f"Audio init failed: {e}")
            pa.terminate()
            return

        audio_buffer = bytearray()
        silence_count = 0
        in_speech = False
        self._status("Listening...")

        while self._running:
            try:
                data = stream.read(FRAME_SIZE, exception_on_overflow=False)
                audio_data = np.frombuffer(data, dtype=np.int16)
                amplitude = np.abs(audio_data).mean()

                if amplitude > self.silence_threshold:
                    audio_buffer.extend(data)
                    silence_count = 0
                    if not in_speech:
                        in_speech = True
                        self._status("Speech detected...")
                else:
                    if in_speech:
                        silence_count += 1
                        if silence_count >= silence_frames_to_cut:
                            if len(audio_buffer) > 16000:  # at least ~1s of audio
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

            except OSError:
                # Stream overflow — skip frame
                continue
            except Exception as e:
                self._status(f"Capture error: {e}")
                time.sleep(0.5)

        if stream:
            stream.stop_stream()
            stream.close()
        pa.terminate()

    def _find_loopback_device(self, pa):
        """Find a WASAPI loopback / Stereo Mix device."""
        import platform

        if platform.system() != "Windows":
            return None

        # Try WASAPI host API first
        wasapi_host = None
        for i in range(pa.get_host_api_count()):
            info = pa.get_host_api_info_by_index(i)
            if "WASAPI" in info.get("name", "").upper():
                wasapi_host = i
                break

        if wasapi_host is not None:
            host_info = pa.get_host_api_info_by_index(wasapi_host)
            for i in range(host_info.get("deviceCount")):
                dev = pa.get_device_info_by_host_api_device_index(wasapi_host, i)
                if dev.get("maxInputChannels", 0) > 0:
                    name = dev.get("name", "").lower()
                    if any(k in name for k in ["loopback", "stereo mix", "what u hear"]):
                        return dev["index"]

        # Fallback: search all devices
        info = pa.get_host_api_info_by_index(0)
        for i in range(info.get("deviceCount")):
            dev = pa.get_device_info_by_host_api_device_index(0, i)
            if dev.get("maxInputChannels", 0) > 0:
                name = dev.get("name", "").lower()
                if any(k in name for k in ["loopback", "stereo mix", "what u hear"]):
                    return dev["index"]

        return None

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

        # Push audio data in chunks
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
