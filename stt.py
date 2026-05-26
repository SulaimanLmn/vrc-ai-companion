"""Speech-to-Text using SpeechRecognition + Azure Speech API.

Two capture modes:
  - "microphone" (default): uses SpeechRecognition library for reliable
    VAD and audio capture, with Azure Speech REST API for transcription.
  - "loopback": captures desktop audio via WASAPI loopback using PyAudio,
    with Azure Speech SDK for transcription.

In microphone mode, SpeechRecognition's listen_in_background() handles
ambient noise calibration, energy-based VAD (voice activity detection),
and phrase segmentation automatically — no manual threshold tuning needed.
"""

import queue
import threading
import time
import numpy as np
import speech_recognition as sr
import pyaudio


class AzureSTT:
    """Speech-to-Text using SpeechRecognition VAD + Azure transcription."""

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
        self.capture_mode = capture_mode

        # SpeechRecognition recognizer
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = silence_threshold
        self.recognizer.dynamic_energy_threshold = True   # auto-adjust
        self.recognizer.pause_threshold = silence_cutoff_sec
        # phrase_time_limit is set per-mode, not on the recognizer

        self._running = False
        self._paused = False
        self._thread = None
        self._stop_listening = None
        self.audio_queue = queue.Queue()
        self.source = None
        self.on_text = None
        self.on_status = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """Start STT capture in a background thread."""
        if self._running:
            return
        self._running = True
        self._paused = False

        if self.capture_mode == "loopback":
            self._start_loopback()
        else:
            self._start_microphone()

    def pause(self):
        """Pause STT transcription — audio is ignored until resume()."""
        self._paused = True
        # Drain pending audio to prevent Azure consumption during TTS
        drained = 0
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                drained += 1
            except queue.Empty:
                break
        print(f"[STT] pause() — transcription paused ({drained} pending audio(s) discarded)")

    def resume(self):
        """Resume STT transcription after pause()."""
        self._paused = False
        print("[STT] resume() — transcription resumed")

    def stop(self):
        """Stop STT capture."""
        print("[STT] stop() called")
        self._running = False
        if self._stop_listening is not None:
            self._stop_listening(wait_for_stop=False)
            self._stop_listening = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        if self.source is not None:
            self.source = None
        self._status("STT stopped")

    def _status(self, msg: str):
        print(f"[STT] {msg}")
        if self.on_status:
            self.on_status(msg)

    # ------------------------------------------------------------------
    # Microphone mode — uses SpeechRecognition
    # ------------------------------------------------------------------

    def _start_microphone(self):
        """Start microphone mode: SpeechRecognition VAD + Azure REST."""
        # Use device's native sample rate (many virtual devices don't support 16kHz)
        sample_rate = self._device_sample_rate()
        try:
            self.source = sr.Microphone(
                device_index=self.device_index,
                sample_rate=sample_rate,
            )
            name = self._device_name()
            print(f"[STT] Using mic device [{self.device_index}]: {name} @ {sample_rate} Hz")
        except Exception as e:
            self._status(f"Mic init failed: {e}")
            self._running = False
            return

        # Calibrate for ambient noise
        with self.source:
            self.recognizer.adjust_for_ambient_noise(self.source)
            # Increase threshold slightly to reduce false triggers
            self.recognizer.energy_threshold = max(
                self.recognizer.energy_threshold * 1.5, self.silence_threshold
            )
            adjusted = self.recognizer.energy_threshold
            print(f"[STT] Calibrated threshold: {adjusted:.0f} (base {self.silence_threshold})")

        # Start background listener — handles VAD automatically
        self._stop_listening = self.recognizer.listen_in_background(
            self.source,
            self._sr_callback,
            phrase_time_limit=10,
        )
        self._status("Listening...")

        # Start queue processor thread
        self._thread = threading.Thread(target=self._process_queue, daemon=True)
        self._thread.start()

    def _sr_callback(self, recognizer, audio: sr.AudioData):
        """SpeechRecognition callback — runs when a phrase is detected."""
        if self._paused:
            return
        self.audio_queue.put(audio)
        # Live amplitude bar for debugging (only if meaningful audio)
        if len(audio.frame_data) > 0:
            samples = np.frombuffer(audio.frame_data, dtype=np.int16)
            amp = np.abs(samples).mean()
            if amp > self.recognizer.energy_threshold * 0.5:
                bars = min(40, int(amp / 100))
                print(f"[STT] ◉ {'█' * bars}{' ' * (40-bars)}  amp={amp:.0f}")

    def _process_queue(self):
        """Background thread: send captured audio to Azure for transcription."""
        while self._running:
            # When paused, drain audio queue silently (ignore speech during TTS)
            while self._paused and not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                except queue.Empty:
                    break

            try:
                audio = self.audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # Safety: discard if paused (handles race where audio was
            # dequeued just before pause() flagged)
            if self._paused:
                continue

            try:
                # Recognize via Azure Speech REST API
                result = self.recognizer.recognize_azure(
                    audio,
                    key=self.subscription_key,
                    location=self.region,
                    language=self.locale,
                )
                # Azure returns (text, confidence) tuple; extract text
                if isinstance(result, tuple):
                    text = (result[0] or "").strip()
                    confidence = result[1] if len(result) > 1 else 1.0
                else:
                    text = (result or "").strip()
                    confidence = 1.0

                if text and confidence > 0.3:
                    print(f"[STT] ✓ Heard: {text}")
                    if self.on_text:
                        self.on_text(text)
                elif text:
                    print(f"[STT] ✗ Low confidence ({confidence:.2f}): {text}")
                else:
                    print(f"[STT] ✗ Azure returned empty")
            except sr.UnknownValueError:
                print(f"[STT] ✗ Azure: no speech recognized")
            except sr.RequestError as e:
                self._status(f"Azure request error: {e}")
            except Exception as e:
                self._status(f"Transcription error: {e}")

    # ------------------------------------------------------------------
    # Loopback mode — uses PyAudio + Azure SDK (preserved from original)
    # ------------------------------------------------------------------

    def _start_loopback(self):
        """Start loopback mode: WASAPI loopback capture + Azure SDK transcription."""
        import azure.cognitiveservices.speech as speechsdk

        speech_config = speechsdk.SpeechConfig(
            subscription=self.subscription_key, region=self.region
        )
        speech_config.speech_recognition_language = self.locale
        speech_config.request_word_level_timestamps()

        TARGET_RATE = 16000
        CHANNELS = 1
        FRAME_SIZE = 1024

        pa = pyaudio.PyAudio()
        stream = None

        try:
            dev_index = self._find_loopback_device(pa)
            if dev_index is None:
                default = pa.get_default_input_device_info()
                dev_index = default["index"]
                self._status("No loopback device found. Using default input.")
                self._status("Tip: Enable 'Stereo Mix' in Windows Sound settings \u2192 Recording tab.")
            else:
                self._status(
                    f"Using loopback device [{dev_index}]: "
                    f"{pa.get_device_info_by_index(dev_index)['name']}"
                )

            device_info = pa.get_device_info_by_index(dev_index)
            CAPTURE_RATE = int(device_info.get('defaultSampleRate', 48000))
            self._status(f"Device native rate: {CAPTURE_RATE} Hz")

            silence_frames_to_cut = int(self.silence_cutoff_sec * CAPTURE_RATE / FRAME_SIZE)
            min_buffer_bytes = CAPTURE_RATE * 2

            stream = pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=CAPTURE_RATE,
                input=True,
                input_device_index=dev_index,
                frames_per_buffer=FRAME_SIZE,
            )
            self._status(
                f"Stream opened at {CAPTURE_RATE} Hz "
                f"\u2014 resampling to {TARGET_RATE} Hz for Azure"
            )
        except Exception as e:
            self._status(f"Audio init failed: {e}")
            pa.terminate()
            return

        # Start a thread to run the capture loop
        self._thread = threading.Thread(
            target=self._loopback_loop,
            args=(pa, stream, speech_config, CAPTURE_RATE, TARGET_RATE,
                  silence_frames_to_cut, min_buffer_bytes, FRAME_SIZE),
            daemon=True,
        )
        self._thread.start()

    def _loopback_loop(self, pa, stream, speech_config, CAPTURE_RATE, TARGET_RATE,
                       silence_frames_to_cut, min_buffer_bytes, FRAME_SIZE):
        """Capture loop for loopback mode (runs in background thread)."""
        audio_buffer = bytearray()
        silence_count = 0
        in_speech = False
        frame_count = 0
        self._status("Loopback capture started")

        while self._running:
            try:
                data = stream.read(FRAME_SIZE, exception_on_overflow=False)
                audio_data = np.frombuffer(data, dtype=np.int16)
                amplitude = np.abs(audio_data).mean()

                frame_count += 1
                if frame_count % 100 == 0 and not in_speech:
                    print(f"[STT] Listening... (amp={amplitude:.0f})")

                if amplitude > 500:
                    audio_buffer.extend(data)
                    silence_count = 0
                    if not in_speech:
                        in_speech = True
                        print(f"[STT] ◉ HEARING  amp={amplitude:.0f}")
                    elif frame_count % 20 == 0:
                        bars = min(40, int(amplitude / 100))
                        print(f"[STT] ◉ {'█' * bars}{' ' * (40-bars)}  amp={amplitude:.0f}")
                else:
                    if in_speech:
                        silence_count += 1
                        if silence_count >= silence_frames_to_cut:
                            if len(audio_buffer) > min_buffer_bytes:
                                buf_arr = np.frombuffer(bytes(audio_buffer), dtype=np.int16)
                                buf_mean = np.abs(buf_arr).mean()
                                print(f"[STT] ○ TRANSCRIBING "
                                      f"({len(audio_buffer)/CAPTURE_RATE/2:.1f}s, "
                                      f"amp={buf_mean:.0f})")
                                if buf_mean > 400:
                                    audio_16k = _resample(
                                        bytes(audio_buffer), CAPTURE_RATE, TARGET_RATE
                                    )
                                    text = _transcribe_azure(speech_config, audio_16k)
                                    if text:
                                        print(f"[STT] ✓ Heard: {text}")
                                        if self.on_text:
                                            self.on_text(text)
                                    else:
                                        print(f"[STT] ✗ No speech recognized")
                            audio_buffer = bytearray()
                            silence_count = 0
                            in_speech = False
                            print("[STT] Listening...")

            except OSError:
                continue
            except Exception as e:
                print(f"[STT] Capture error: {e}")
                time.sleep(0.5)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _device_name(self) -> str:
        pa = pyaudio.PyAudio()
        try:
            if self.device_index is not None:
                return pa.get_device_info_by_index(self.device_index)["name"]
            return pa.get_default_input_device_info()["name"]
        except Exception:
            return "unknown"
        finally:
            pa.terminate()

    def _device_sample_rate(self) -> int:
        """Get the device's native sample rate (fallback 48000)."""
        pa = pyaudio.PyAudio()
        try:
            if self.device_index is not None:
                info = pa.get_device_info_by_index(self.device_index)
                return int(info.get('defaultSampleRate', 48000))
            return 48000
        except Exception:
            return 48000
        finally:
            pa.terminate()

    @staticmethod
    def _find_loopback_device(pa):
        """Find a WASAPI loopback / Stereo Mix device."""
        import platform
        if platform.system() != "Windows":
            return None

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

        info = pa.get_host_api_info_by_index(0)
        for i in range(info.get("deviceCount")):
            dev = pa.get_device_info_by_host_api_device_index(0, i)
            if dev.get("maxInputChannels", 0) > 0:
                name = dev.get("name", "").lower()
                if any(k in name for k in ["loopback", "stereo mix", "what u hear"]):
                    return dev["index"]

        return None

    @staticmethod
    def list_devices():
        """List available audio input devices, highlighting loopback devices."""
        pa = pyaudio.PyAudio()
        try:
            wasapi_host = None
            for i in range(pa.get_host_api_count()):
                info = pa.get_host_api_info_by_index(i)
                if "WASAPI" in info.get("name", "").upper():
                    wasapi_host = i
                    break
            devices = []
            target_host = wasapi_host if wasapi_host is not None else 0
            host_info = pa.get_host_api_info_by_index(target_host)
            for i in range(host_info.get("deviceCount")):
                dev = pa.get_device_info_by_host_api_device_index(target_host, i)
                if dev.get("maxInputChannels", 0) > 0:
                    name = dev.get("name", "")
                    is_loopback = any(
                        k in name.lower()
                        for k in ["loopback", "stereo mix", "what u hear", "wasapi loopback"]
                    )
                    marker = " [LOOPBACK]" if is_loopback else ""
                    devices.append((dev["index"], name + marker))
            return devices
        finally:
            pa.terminate()


# ------------------------------------------------------------------
# Module-level helpers (used by loopback mode)
# ------------------------------------------------------------------

def _resample(audio_bytes: bytes, orig_rate: int, target_rate: int) -> bytes:
    """Resample 16-bit mono PCM audio via linear interpolation."""
    if orig_rate == target_rate:
        return audio_bytes
    audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
    ratio = target_rate / orig_rate
    new_len = max(1, int(len(audio) * ratio))
    indices = np.linspace(0, len(audio) - 1, new_len)
    resampled = np.interp(indices, np.arange(len(audio)), audio)
    return resampled.astype(np.int16).tobytes()


def _transcribe_azure(speech_config, audio_bytes: bytes) -> str:
    """Send audio bytes to Azure Speech SDK and return recognized text."""
    import azure.cognitiveservices.speech as speechsdk

    push_stream = speechsdk.audio.PushAudioInputStream(
        speechsdk.audio.AudioStreamFormat(
            samples_per_second=16000, bits_per_sample=16, channels=1,
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
            done.set()

    recognizer.recognized.connect(on_recognized)

    recognizer.start_continuous_recognition()

    chunk_size = 3200
    for i in range(0, len(audio_bytes), chunk_size):
        push_stream.write(audio_bytes[i:i + chunk_size])

    # Brief pause before closing to let Azure process
    for _ in range(5):
        if result_text:
            break
        time.sleep(0.1)
    push_stream.close()

    # Wait up to 15s for result
    for _ in range(150):
        if done.is_set() or result_text:
            break
        time.sleep(0.1)

    try:
        recognizer.stop_continuous_recognition()
    except Exception:
        pass

    return result_text.strip()
