# Audio Test Files for STT Testing

Place audio files here to test Speech-to-Text.

## Supported formats
- `.wav` - Preferred (16kHz mono PCM)
- `.mp3` - Also supported
- `.ogg` - Also supported

## Recommended test phrase
Record a short audio file (1-3 seconds) saying:
**"Hello from NeuroClone test"**

## How to test
1. Record your test audio and save as `test.wav` (or any supported format)
2. Click **"Test STT"** button in the web UI at http://localhost:5000
3. The transcription result will appear in an alert

## Notes
- Azure STT works best with clear speech in English
- Keep recordings short (under 10 seconds) for quick testing
- For Windows: Use Voice Recorder app or Audacity to create WAV files