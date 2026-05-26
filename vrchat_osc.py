"""VRChat OSC ChatBox integration.

Sends chat messages and typing indicators via OSC to VRChat.
Requires OSC enabled in VRChat settings.
"""

import threading
import time
from pythonosc import udp_client


class ChatBox:
    def __init__(self, ip="127.0.0.1", port=9000):
        self.client = udp_client.SimpleUDPClient(ip, port)
        self._typing = False
        self._lock = threading.Lock()

    def send_message(self, text: str, visible: bool = True):
        """Send a chat message to VRChat."""
        if not text or not text.strip():
            return
        self._set_typing(False)
        # VRChat ChatBox API
        self.client.send_message(
            "/chatbox/input", [text, visible, True]
        )
        time.sleep(0.1)
        self._set_typing(False)

    def start_typing(self):
        """Show typing indicator."""
        self._set_typing(True)

    def _set_typing(self, state: bool):
        with self._lock:
            if self._typing != state:
                self._typing = state
                self.client.send_message("/chatbox/typing", [state])

    def stream_text(self, text: str, delay: float = 0.04):
        """Stream text character-by-character with typing indicator."""
        self._set_typing(True)
        for i in range(1, len(text) + 1):
            chunk = text[:i]
            self.client.send_message(
                "/chatbox/input", [chunk, True, False]
            )
            time.sleep(delay)
        # Final message
        self.client.send_message(
            "/chatbox/input", [text, True, True]
        )
        time.sleep(0.1)
        self._set_typing(False)
