"""Speech-to-Text — WakeWordSTT (Vosk keyphrase + PyAudio + Azure) and legacy AzureSTT.

WakeWordSTT architecture:
  Single PyAudio stream → resample to 16 kHz → Vosk keyphrase spotting
  On match: continue recording → VAD silence → Azure STT → callback(text)

  Fallback (no Vosk model / vosk not installed): energy-based VAD trigger.
"""

import json
import os
import queue
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import re
import time as _time


# ── Shared latency marker (imported by main.py) ──

_latency_ref = None


def reset_latency():
    """Reset the latency reference clock. Call at the start of each new interaction."""
    global _latency_ref
    _latency_ref = None


def latency_mark(label: str, extra: str = ""):
    """Print a human-readable latency stamp relative to the first mark.

    Usage:
        latency_mark("WAKE trigger")
        latency_mark("LLM done",  "dur=5.02s")
    """
    global _latency_ref
    now = _time.monotonic()
    if _latency_ref is None:
        _latency_ref = now
    delta = now - _latency_ref
    print(f"[LATENCY] {label:<30s} {delta:>7.3f}s  {extra}")

import azure.cognitiveservices.speech as speechsdk
import numpy as np
import pyaudio
import speech_recognition as sr


def _keyword_in_text(keyword: str, text: str) -> bool:
    """Check if *keyword* appears as a whole word / phrase in *text*.

    Uses word-boundary matching so 'the single' does not match
    'these ingle' or 'another single word'.  Multi-word keywords
    are matched as an exact phrase within word boundaries.
    """
    if not keyword or not text:
        return False
    pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
    return bool(re.search(pattern, text.lower()))


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


# ── Vosk model auto-download ──

VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
VOSK_MODEL_URL = (
    f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"
)


def _ensure_vosk_model() -> str:
    """Download and extract the Vosk model if not already cached.

    Returns path to the model directory.
    """
    model_dir = Path(__file__).resolve().parent / "vosk_models"
    model_path = model_dir / VOSK_MODEL_NAME

    if model_path.is_dir():
        return str(model_path)

    model_dir.mkdir(parents=True, exist_ok=True)
    zip_path = model_dir / f"{VOSK_MODEL_NAME}.zip"

    print(f"[STT] Downloading Vosk model ({VOSK_MODEL_NAME}, ~40 MB)...")
    urllib.request.urlretrieve(VOSK_MODEL_URL, zip_path)
    print("[STT] Extracting...")

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(model_dir)

    zip_path.unlink()
    print("[STT] Vosk model ready")
    return str(model_path)


class WakeWordSTT:
    """Wake-word gated or energy-VAD speech-to-text.

    Architecture:
      Single PyAudio stream → resample to 16 kHz → Vosk keyphrase spotting
      On match: continue recording → VAD silence → Azure STT → callback(text)

      When Vosk is unavailable (not installed / model download failed), the
      class falls back to energy-based VAD — any sound above threshold
      triggers recording.

    No API keys or sign-ups required — Vosk is 100% open-source offline ASR.
    """

    def __init__(
        self,
        subscription_key: str,
        region: str,
        device_index: int = -1,
        wake_keyword: str = "computer",
        wake_mode: str = "vosk",
        oww_model: str = "",
        silence_threshold: int = 500,
        silence_cutoff_sec: float = 2.0,
    ):
        self.subscription_key = subscription_key
        self.region = region
        self.device_index = device_index if device_index >= 0 else None
        self.wake_keyword = wake_keyword
        self.wake_mode = wake_mode
        self.oww_model_name = oww_model  # selected openWakeWord model name
        self.silence_threshold = silence_threshold
        self.silence_cutoff_sec = silence_cutoff_sec

        self._running = False
        self._paused = False
        self._thread = None
        self._pa = None
        self._stream = None
        self._vosk_recognizer = None
        self._oww_model = None  # openWakeWord model
        self._oww_keywords = []  # keywords extracted from model filenames
        self._oww_buffer = b""  # 16kHz audio buffer for openWakeWord
        self._sample_rate = 16000
        self._dev_index = -1
        self._use_wake_word = wake_keyword and wake_mode == "vosk"
        self._resume_time = 0.0  # timestamp of last resume — for cooldown after TTS

        self.on_text = None       # callback(text)
        self.on_status = None     # callback(msg)
        self.on_level = None      # callback(amplitude) — for UI mic meter

    # ── Public API ──

    def start(self):
        """Start STT — opens audio stream and begins detection."""
        if self._running:
            return
        self._running = True
        self._paused = False
        self._thread = threading.Thread(target=self._audio_loop, daemon=True)
        self._thread.start()

    def pause(self):
        """Pause detection (e.g. during TTS playback)."""
        self._paused = True

    def resume(self):
        """Resume detection after pause."""
        self._paused = False
        self._resume_time = _time.time()
        # Clear Vosk state so it doesn't trigger on stale context from
        # before the pause (e.g. keyword fragments from the previous utterance).
        if self._vosk_recognizer:
            self._vosk_recognizer.Reset()
        # Clear openWakeWord internal state so it starts fresh
        if self._oww_model:
            self._oww_model.reset()

    def stop(self):
        """Stop STT and release resources."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self._cleanup()

    # ── Internal ──

    def _status(self, msg: str):
        print(f"[STT] {msg}")
        if self.on_status:
            self.on_status(msg)

    def _cleanup(self):
        if self._stream:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

    # ── Audio loop ──

    def _audio_loop(self):
        """Main loop: read frames → Vosk keyphrase / energy trigger → record → transcribe."""
        self._pa = pyaudio.PyAudio()

        # Resolve audio device
        dev_index = self.device_index
        if dev_index is None:
            dev_info = self._pa.get_default_input_device_info()
            dev_index = dev_info["index"]
        dev_info = self._pa.get_device_info_by_index(dev_index)
        sample_rate = int(dev_info.get("defaultSampleRate", 16000))
        self._sample_rate = sample_rate
        self._dev_index = dev_index

        print(f"[STT] Device [{dev_index}]: {dev_info['name']} @ {sample_rate} Hz")

        # ── Initialize Vosk (wake word keyphrase spotting) ──
        frame_length = 512  # PyAudio read size
        if self._use_wake_word:
            try:
                import vosk as _vosk
                vosk_model_path = _ensure_vosk_model()
                _vosk_model = _vosk.Model(vosk_model_path)
                self._vosk_recognizer = _vosk.KaldiRecognizer(_vosk_model, 16000)
                # Enable partial word output for better keyword spotting
                try:
                    self._vosk_recognizer.SetPartialWords(True)
                except AttributeError:
                    pass  # older vosk versions don't have this
                self._status(f"Wake word: '{self.wake_keyword}'")
            except ImportError:
                self._status("vosk not installed — fallback to energy-VAD")
                self._use_wake_word = False
            except Exception as e:
                self._status(f"Vosk init failed: {e} — fallback to energy-VAD")
                self._use_wake_word = False
        else:
            self._status("Wake word disabled — using continuous energy-VAD")

        # ── Initialize openWakeWord (if mode selected) ──
        if self.wake_mode == "openwakeword":
            try:
                import openwakeword as _oww
                from openwakeword.model import Model as _OWWModel
                import glob as _glob
                model_dir = os.path.join(os.path.dirname(__file__), "models", "openwakeword")
                # Load only the selected model, or all models if none selected
                if self.oww_model_name:
                    model_path = os.path.join(model_dir, self.oww_model_name)
                    # Try with common extensions
                    for ext in [".onnx", ".tflite"]:
                        f = model_path + ext
                        if os.path.exists(f):
                            model_files = [f]
                            break
                    else:
                        model_files = []
                        self._status(f"OWW model '{self.oww_model_name}' not found, scanning folder")
                else:
                    model_files = []
                if not model_files:
                    model_files = _glob.glob(os.path.join(model_dir, "*.tflite")) + \
                                  _glob.glob(os.path.join(model_dir, "*.onnx"))
                if model_files:
                    self._oww_model = _OWWModel(wakeword_models=model_files, vad_threshold=0.5)
                    # Extract keywords from model filenames (e.g., "Amelia.onnx" → "Amelia")
                    self._oww_keywords = list(self._oww_model.models.keys())
                    self._status(f"openWakeWord models: {self._oww_keywords}")
                else:
                    self._status("No openWakeWord models found in models/openwakeword/ — fallback to VAD")
                    self.wake_mode = "vad"
                    self._use_wake_word = False
            except ImportError:
                self._status("openwakeword not installed — fallback to VAD")
                self.wake_mode = "vad"
                self._use_wake_word = False
            except Exception as e:
                self._status(f"openWakeWord init failed: {e} — fallback to VAD")
                self.wake_mode = "vad"
                self._use_wake_word = False

        # ── Open PyAudio stream ──
        try:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=sample_rate,
                input=True,
                input_device_index=dev_index,
                frames_per_buffer=frame_length,
            )
        except Exception as e:
            self._status(f"PyAudio stream failed: {e}")
            self._running = False
            return

        # Azure speech config (reused for every transcription)
        speech_config = speechsdk.SpeechConfig(
            subscription=self.subscription_key, region=self.region
        )
        speech_config.speech_recognition_language = "en-US"

        # Ring buffer — keeps ~1.5 s of audio at device rate
        ring_maxlen = max(10, int(1.5 * sample_rate / frame_length))
        import collections
        ring_buffer = collections.deque(maxlen=ring_maxlen)

        self._status("Listening for wake word..." if self._use_wake_word else "Listening...")

        cooldown_until = 0.0  # timestamp — prevent rapid re-triggers
        _level_last = [0.0]   # throttle mic meter updates to ~10 Hz

        while self._running:
            if self._paused:
                time.sleep(0.05)
                continue

            # Read one frame from device
            try:
                pcm = self._stream.read(frame_length, exception_on_overflow=False)
            except Exception as e:
                self._status(f"Read error: {e}")
                time.sleep(0.1)
                continue

            pcm_array = np.frombuffer(pcm, dtype=np.int16)
            amp = np.abs(pcm_array).mean()
            # Throttled level callback for UI mic meter (~10 Hz)
            if self.on_level and _time.time() - _level_last[0] > 0.1:
                self.on_level(amp)
                _level_last[0] = _time.time()
            ring_buffer.append(pcm_array.copy())

            # ── Detect trigger ──
            triggered = False

            # Post-resume cooldown: don't detect for 1.5s after TTS ends
            if _time.time() - self._resume_time < 3.0:
                pass  # skip detection, just accumulate ring buffer
            elif self._use_wake_word and self._vosk_recognizer:
                # Energy gate: only feed SILENT frames to Vosk if the model
                # has received speech recently (keep it primed), otherwise
                # skip entirely to prevent hallucinating keywords in noise.
                silent = amp <= self.silence_threshold
                if not silent:
                    pcm_16k = _resample(pcm, sample_rate, 16000)
                    is_final = self._vosk_recognizer.AcceptWaveform(pcm_16k)
                    if is_final:
                        result = json.loads(self._vosk_recognizer.Result())
                        if _keyword_in_text(self.wake_keyword, result.get("text", "")):
                            latency_mark("WAKE trigger")
                            self._status("Wake word detected!")
                            triggered = True
                    else:
                        partial = json.loads(self._vosk_recognizer.PartialResult())
                        if _keyword_in_text(self.wake_keyword, partial.get("partial", "")):
                            latency_mark("WAKE trigger")
                            self._status("Wake word detected!")
                            triggered = True
                # Silent frames are NOT fed to Vosk — prevents phantom detections
            elif self.wake_mode == "openwakeword" and self._oww_model:
                # openWakeWord detection — buffer 16kHz audio to 1280-sample frames
                pcm_16k = _resample(pcm, sample_rate, 16000)
                self._oww_buffer += pcm_16k
                OWW_FRAME = 2560  # 160ms at 16kHz
                while len(self._oww_buffer) >= OWW_FRAME:
                    chunk = self._oww_buffer[:OWW_FRAME]
                    self._oww_buffer = self._oww_buffer[OWW_FRAME:]
                    chunk_array = np.frombuffer(chunk, dtype=np.int16)
                    prediction = self._oww_model.predict(chunk_array)
                    for model_name, score in prediction.items():
                        if score > 0.6:
                            latency_mark("WAKE trigger")
                            self._status(f"Wake word detected ({model_name}: {score:.2f})")
                            triggered = True
                            break
                    if triggered:
                        break
            else:
                # Energy-based VAD
                if amp > self.silence_threshold:
                    triggered = True

            if triggered:
                now = time.time()
                if now < cooldown_until:
                    triggered = False  # still in cooldown
                else:
                    cooldown_until = now + 3.0  # 3 s cooldown

            if triggered:
                self._record_utterance(ring_buffer, speech_config, sample_rate, frame_length)
                # Reset Vosk after each cycle so it doesn't immediately
                # re-detect the keyword in residual / ongoing audio.
                if self._vosk_recognizer:
                    self._vosk_recognizer.Reset()
                # Clear openWakeWord internal state (prediction buffer, etc.)
                if self._oww_model:
                    self._oww_model.reset()
                self._status("Listening for wake word..." if self._use_wake_word else "Listening...")

        self._cleanup()

    # ── Recording + VAD + Transcription ──

    def _record_utterance(self, ring_buffer, speech_config, sample_rate, frame_length):
        """Record speech after trigger → VAD end → resample → Azure transcribe."""
        self._status("recording")
        capture_parts = list(ring_buffer)  # includes the trigger frame

        silence_count = 0
        silence_frames = int(self.silence_cutoff_sec * sample_rate / frame_length)
        min_frames = int(0.3 * sample_rate / frame_length)   # at least 300 ms
        max_frames = int(15 * sample_rate / frame_length)    # at most 15 s
        total_frames = 0

        while self._running and total_frames < max_frames:
            if self._paused:
                time.sleep(0.05)
                continue

            try:
                data = self._stream.read(frame_length, exception_on_overflow=False)
            except Exception:
                time.sleep(0.05)
                continue

            audio_data = np.frombuffer(data, dtype=np.int16)
            capture_parts.append(audio_data)
            total_frames += 1

            amp = np.abs(audio_data).mean()

            if amp > self.silence_threshold:
                silence_count = 0
            else:
                silence_count += 1

            # Live amplitude bar
            if total_frames % 10 == 0:
                bars = min(30, int(amp / 150))
                print(
                    f"\r[STT] {'█' * bars}{' ' * (30 - bars)}  amp={amp:.0f}  {total_frames}f",
                    end="", flush=True,
                )

            # End: enough silence after enough audio
            if silence_count >= silence_frames and total_frames >= min_frames:
                break

        print()

        elapsed = total_frames * frame_length / sample_rate
        latency_mark("capture end", f"dur={elapsed:.1f}s  frames={total_frames}")
        self._status(f"Captured {elapsed:.1f}s ({total_frames} frames)")
        self._status("transcribing")

        # ── Trim trailing silence ──
        full_audio = np.concatenate(capture_parts)
        if len(full_audio) < sample_rate * 0.2:
            self._status("Audio too short, skipped")
            return

        trim_window = frame_length * 2
        trim_end = len(full_audio)
        while trim_end > trim_window * 2:
            chunk = full_audio[trim_end - trim_window:trim_end]
            if np.abs(chunk).mean() > self.silence_threshold * 0.5:
                break
            trim_end -= trim_window
        if trim_end < len(full_audio):
            full_audio = full_audio[:trim_end]

        # ── Reject if no actual speech in the capture ──
        # (only needed in VAD mode — Vosk/OWW already validate the trigger)
        if self.wake_mode == "vad":
            speech_energy = np.abs(full_audio).mean()
            if speech_energy < self.silence_threshold * 0.4:
                self._status(f"False trigger — speech too quiet ({speech_energy:.0f} < {self.silence_threshold * 0.4:.0f})")
                return

        # ── Resample to 16 kHz ──
        TARGET = 16000
        if sample_rate != TARGET:
            audio_float = full_audio.astype(np.float32)
            ratio = TARGET / sample_rate
            new_len = max(1, int(len(audio_float) * ratio))
            indices = np.linspace(0, len(audio_float) - 1, new_len)
            resampled = np.interp(indices, np.arange(len(audio_float)), audio_float)
            audio_16k = resampled.astype(np.int16).tobytes()
        else:
            audio_16k = full_audio.tobytes()

        # ── Azure transcription ──
        text = _transcribe_azure(speech_config, audio_16k)
        if text:
            # Strip the wake keyword from the transcribed text so the
            # LLM only sees the actual user request after the trigger.
            cleaned = text.strip()
            if self._use_wake_word and _keyword_in_text(self.wake_keyword, cleaned):
                # Vosk mode: strip the configured keyword
                pattern = r'\b' + re.escape(self.wake_keyword.lower()) + r'\b'
                cleaned = re.sub(pattern, '', cleaned.lower(), count=1).strip()
                if cleaned:
                    cleaned = cleaned[0].upper() + cleaned[1:]
                cleaned = cleaned.lstrip(",.;:!? ").strip()
            elif self.wake_mode == "openwakeword":
                # openWakeWord mode: strip the detected model keyword
                for kw in self._oww_keywords:
                    if _keyword_in_text(kw, cleaned):
                        pattern = r'\b' + re.escape(kw.lower()) + r'\b'
                        cleaned = re.sub(pattern, '', cleaned.lower(), count=1).strip()
                        if cleaned:
                            cleaned = cleaned[0].upper() + cleaned[1:]
                        cleaned = cleaned.lstrip(",.;:!? ").strip()
                        break
            if cleaned:
                latency_mark("transcribe done")
                print(f"[STT] ✓ {cleaned}")
                if self.on_text:
                    self.on_text(cleaned)
            else:
                self._status("Only wake word, no request")
        else:
            self._status("No speech recognized")
