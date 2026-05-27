"""Resolve PyAudio device index to WASAPI device UUID.

Run this ONCE to find the WASAPI UUID for your TTS output device.
The UUID is used by Azure Speech SDK's AudioOutputConfig to route
audio to a specific device.

Note: This script temporarily imports comtypes/pycaw in a separate
process. The main app (tts.py) never imports these packages.

Usage:
    python resolve_devices.py                  # list all output devices
    python resolve_devices.py <index>          # resolve index to UUID
    python resolve_devices.py --save           # save all to wasapi_devices.json
"""

import json
import sys
import os

OUTPUT_FILE = "wasapi_devices.json"


def get_devices():
    """Return dict of {friendly_name: wasapi_uuid} for OUTPUT devices (render, any state).

    Filters by UUID prefix: render devices start with {0.0.0.00000000}.
    """
    import comtypes

    comtypes.CoInitialize()
    try:
        from pycaw.utils import AudioUtilities

        result = {}
        for dev in AudioUtilities.GetAllDevices():
            try:
                name = dev.FriendlyName
                dev_id = dev.id
                # Only include render (output) devices — they start with
                # {0.0.0.00000000} vs capture which starts with {0.0.1.00000000}
                if name and dev_id and dev_id.startswith("{0.0.0.00000000}"):
                    result[name] = dev_id
            except Exception:
                continue
        return result
    finally:
        comtypes.CoUninitialize()


def main():
    if "--save" in sys.argv:
        devices = get_devices()
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(devices, f, indent=2)
        print(f"Saved {len(devices)} devices to {OUTPUT_FILE}")
        print(f"\nThen set in .env:\nTTS_OUTPUT_DEVICE_UUID=<uuid>")
        return

    devices = get_devices()

    if len(sys.argv) > 1:
        try:
            index = int(sys.argv[1])
        except ValueError:
            print("Usage: python resolve_devices.py [index|--save]")
            return

        import pyaudio

        pa = pyaudio.PyAudio()
        try:
            info = pa.get_device_info_by_index(index)
            name = info["name"]
        except Exception as e:
            print(f"Device [{index}] not found: {e}")
            pa.terminate()
            return
        pa.terminate()

        uuid = devices.get(name, "")
        if uuid:
            print(f"[{index}] {name}")
            print(f"  WASAPI UUID: {uuid}")
            print(f"\nSet in .env:\nTTS_OUTPUT_DEVICE_UUID={uuid}")
        else:
            print(f"[{index}] {name}")
            print("  WASAPI UUID not found (device may not be active)")
            print("  Available devices:")
            for n, u in sorted(devices.items()):
                print(f"    {n}")
    else:
        import pyaudio

        pa = pyaudio.PyAudio()
        print("Active output devices with WASAPI UUIDs:\n")
        seen = set()
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxOutputChannels", 0) > 0:
                name = info["name"]
                if name not in seen:
                    seen.add(name)
                    uuid = devices.get(name, "")
                    marker = "  ✓" if uuid else ""
                    print(f'  [{i:>3}]  {name}')
                    if uuid:
                        print(f'         UUID: {uuid}')
        pa.terminate()

        print(f"\nUsage:")
        print(f"  python resolve_devices.py <index>  -- resolve device index to UUID")
        print(f"  python resolve_devices.py --save    -- save all to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
