"""Speech-to-Text using Azure Speech SDK (streaming).

Captures microphone audio and transcribes in real-time via Azure Speech Service.
Uses a VAD-based loop: record until silence detected, then get final result.
"""

import os
import time
import queue
import threading
import numpy as np


class AzureSTT:
    """Azure Speech SDK STT with push stream.

    Continuously captures microphone input and sends to Azure for transcription.
    Fires callback with final recognized text.
    """

    def __init__(self, subscription_key: str, region: str, device_index: int = -1,
                 locale: str = "en-US", silence_threshold: int = 500,
                 silence_cutoff_sec: float = 2.0):
        self.subscription_key = subscription_key
        self.region = region
        self.device_index = device_index if device_index >= 0 else None
        self.locale = locale
        self.silence_threshold = silence_threshold
        self.silence_cutoff_sec = silence_cutoff_sec
        self._running = False
        self._thread = None
        self.on_text = None      # callback(text: str)
        self.on_status = None    # callback(status: str)
        self.on_partial = None   # callback(partial_text: str) for interim results
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
        """Loop: detect speech segments, stream to Azure, emit results."""
        import azure.cognitiveservices.speech as speechsdk

        speech_config = speechsdk.SpeechConfig(
            subscription=self.subscription_key, region=self.region
        )
        speech_config.speech_recognition_language = self.locale
        speech_config.request_word_level_timestamps()

        # Silence detection params
        SAMPLE_RATE = 16000
        CHANNELS = 1
        BITS_PER_SAMPLE = 16
        FRAME_SIZE = 1024  # ~64ms per frame at 16kHz
        silence_frames_to_cut = int(self.silence_cutoff_sec * SAMPLE_RATE / FRAME_SIZE)

        try:
            import pyaudio
            pa = pyaudio.PyAudio()

            dev_index = self.device_index
            if dev_index is None:
                # Use default input device
                default = pa.get_default_input_device_info()
                dev_index = default["index"]

            self._status(f"Using audio device: {pa.get_device_info_by_index(dev_index)['name']}")

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
            return

        # Buffer for current speech segment
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
                    # Speech detected
                    audio_buffer.extend(data)
                    silence_count = 0
                    if not in_speech:
                        in_speech = True
                        self._status("Speech detected...")
                else:
                    if in_speech:
                        silence_count += 1
                        audio_buffer.extend(data)  # keep trailing silence briefly
                        if silence_count >= silence_frames_to_cut:
                            # End of utterance — transcribe
                            if len(audio_buffer) > 16000:  # at least ~1 second
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
                self._status(f"STT capture error: {e}")
                time.sleep(0.5)

        stream.stop_stream()
        stream.close()
        pa.terminate()

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
