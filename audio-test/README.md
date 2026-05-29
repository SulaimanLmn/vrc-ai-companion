# Audio Test Files for STT Testing

Place audio files here to test speech-to-text via "Test STT" in the web UI.

## Supported formats
- `.wav` — Preferred (16 kHz mono PCM)
- `.mp3` — Also supported
- `.ogg` — Also supported

## How to test
1. Record a short audio file (1-5 seconds) and save as `test.wav`
2. Click **"Test STT"** button in the web UI at `http://localhost:5000`
3. The transcription result will appear in an alert

## Notes
- Works with both `WakeWordSTT` (Vosk + Azure) and legacy `AzureSTT` modes
- For wake word testing: record a file containing your `WAKE_KEYWORD` phrase (e.g., "computer what's the weather")
- For Azure STT testing: any clear English speech works
- For Windows: Use Voice Recorder app or Audacity to create WAV files

## Tips
- Keep recordings under 10 seconds
- 16 kHz mono WAV gives best results (no resampling needed)
- Higher sample rates are resampled automatically
