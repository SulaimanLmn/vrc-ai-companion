# CLI Flags

## `python main.py`

| Flag | Description |
|------|-------------|
| *(no flag)* | Starts the app with web UI at `http://localhost:5000` |
| `--list-devices` | Lists all audio input and output devices with their PyAudio index numbers |
| `--list-windows` | Lists all visible window titles (for `VISION_CAPTURE_WINDOW`) |

### Examples

```bash
# List audio devices (find your mic and loopback indices)
python main.py --list-devices

# List window titles (find the window name for screen capture)
python main.py --list-windows

# Normal startup
python main.py
```

---

## `python resolve_devices.py`

Helper script to resolve a PyAudio device index to its WASAPI UUID (required for TTS output routing).

| Usage | Description |
|-------|-------------|
| `python resolve_devices.py <index>` | Prints the WASAPI UUID for the given device index |

### Example

```bash
# Find the UUID for audio device index 5
python resolve_devices.py 5
```

Then set the UUID in `.env` as `TTS_OUTPUT_DEVICE_UUID`.
