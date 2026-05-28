"""Vision module: screen capture using hybrid approach.

1. Try PrintWindow (fast, works for non-DirectX windows even when behind others)
2. Detect if result is all black (DirectX game) → fall back to mss screen capture
3. For mss capture: bring window to front, capture client area via ClientToScreen
4. Final fallback: full monitor capture
"""

import io
import os
import time
import numpy as np
from PIL import Image

_DEBUG_DIR = os.path.join(os.path.dirname(__file__) or ".", "vision-image")
_DEBUG_FILE = os.path.join(_DEBUG_DIR, "latest.png")


def _save_debug_image(image_bytes: bytes):
    try:
        os.makedirs(_DEBUG_DIR, exist_ok=True)
        with open(_DEBUG_FILE, "wb") as f:
            f.write(image_bytes)
    except Exception:
        pass


def list_window_titles():
    """Return list of visible window titles."""
    try:
        import win32gui
    except ImportError:
        return []
    titles = []
    def enum(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t:
                titles.append(t)
    win32gui.EnumWindows(enum, None)
    return sorted(set(titles))


def _capture_monitor(monitor_index: int = 1) -> bytes | None:
    """Fallback: capture the entire primary monitor."""
    try:
        import mss
        with mss.mss() as sct:
            mon = sct.monitors[monitor_index]
            sct_img = sct.grab(mon)
            img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            _save_debug_image(buf.getvalue())
            print(f"[VISION] Captured monitor ({mon['width']}x{mon['height']})")
            return buf.getvalue()
    except Exception as e:
        print(f"[VISION] Monitor capture failed: {e}")
        return None


def _capture_window(title_substring: str = "VRChat") -> bytes | None:
    """Capture a window by title. Hybrid approach: PrintWindow → mss fallback."""
    try:
        import win32gui
        import win32con
    except ImportError:
        return _capture_monitor()

    def find(hwnd, found):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t and title_substring.lower() in t.lower():
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w >= 200 and h >= 200:
                    found.append((hwnd, t, w * h))

    candidates = []
    win32gui.EnumWindows(find, candidates)
    if not candidates:
        print(f"[VISION] No window >=200px matching '{title_substring}' found")
        return _capture_monitor()

    candidates.sort(key=lambda x: x[2], reverse=True)
    hwnd, title, _ = candidates[0]

    # Get client area and window screen coordinates
    client_rect = win32gui.GetClientRect(hwnd)
    width = client_rect[2]
    height = client_rect[3]
    win_rect = win32gui.GetWindowRect(hwnd)
    win_left, win_top, win_right, win_bottom = win_rect

    if width <= 0 or height <= 0:
        return _capture_monitor()

    # --- Attempt 1: PrintWindow (fast, works for GDI apps behind other windows) ---
    try:
        import win32ui

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)

        # PW_CLIENTONLY | PW_CORRECT_ALPHA = 3
        # Corrected alpha blending helps capture DirectX game content
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        pw = user32.PrintWindow
        pw.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        pw.restype = wintypes.BOOL
        result = pw(hwnd, save_dc.GetSafeHdc(), 3)

        bmp_info = bitmap.GetInfo()
        bmp_str = bitmap.GetBitmapBits(True)
        pw_img = Image.frombuffer(
            "RGB", (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_str, "raw", "BGRX", 0, 1
        )

        win32gui.DeleteObject(bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)

        # Check if mostly black (DirectX content not captured by PrintWindow)
        pw_arr = np.array(pw_img)
        if pw_arr.mean() > 10 and (pw_arr.sum(axis=2) < 30).mean() < 0.9:
            buf = io.BytesIO()
            pw_img.save(buf, format="PNG")
            _save_debug_image(buf.getvalue())
            print(f"[VISION] Captured '{title}' ({width}x{height}) via PrintWindow")
            return buf.getvalue()
        else:
            print(f"[VISION] PrintWindow black (DirectX), falling back to screen capture")

    except Exception as e:
        print(f"[VISION] PrintWindow failed: {e}")

    # --- Attempt 2: mss screen capture at window's client coordinates ---
    try:
        import mss
    except ImportError:
        return _capture_monitor()

    try:
        # Bring window to front for capture
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.12)

        # Client area in screen coordinates
        tl = win32gui.ClientToScreen(hwnd, (0, 0))
        br = win32gui.ClientToScreen(hwnd, (width, height))

        with mss.mss() as sct:
            region = {
                "left": tl[0], "top": tl[1],
                "width": br[0] - tl[0],
                "height": br[1] - tl[1],
            }
            sct_img = sct.grab(region)
            img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            _save_debug_image(buf.getvalue())
            print(f"[VISION] Captured '{title}' ({img.width}x{img.height}) via screen capture")
            return buf.getvalue()

    except Exception as e:
        print(f"[VISION] Screen capture failed: {e}")
        return _capture_monitor()
