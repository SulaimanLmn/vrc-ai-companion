"""VRChat OSC ChatBox integration.

Sends chat messages and typing indicators via OSC to VRChat.
Requires OSC enabled in VRChat settings.

VRChat's chatbox has a 144 character limit. Messages longer than
that are split into sequential chunks at natural break points
(sentence boundaries). Each chunk appears as a complete message
after a brief typing delay — no character-by-character streaming
(UDP packet ordering makes char-by-char unreliable).
"""

import threading
import time
from pythonosc import udp_client

VRC_CHATBOX_MAX = 144  # VRChat character limit


def _split_into_chunks(text: str) -> list[str]:
    """Split text into chunks at natural break points within the limit."""
    if not text:
        return []
    if len(text) <= VRC_CHATBOX_MAX:
        return [text]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= VRC_CHATBOX_MAX:
            chunks.append(remaining)
            break

        cutoff = remaining.rfind('. ', 0, VRC_CHATBOX_MAX)
        if cutoff < 0:
            cutoff = remaining.rfind('! ', 0, VRC_CHATBOX_MAX)
        if cutoff < 0:
            cutoff = remaining.rfind('? ', 0, VRC_CHATBOX_MAX)
        if cutoff < 0:
            cutoff = remaining.rfind(', ', 0, VRC_CHATBOX_MAX)
        if cutoff < 0:
            cutoff = remaining.rfind('; ', 0, VRC_CHATBOX_MAX)
        if cutoff < 0:
            cutoff = remaining.rfind(' ', 0, VRC_CHATBOX_MAX)
        if cutoff < 0:
            cutoff = VRC_CHATBOX_MAX

        chunk = remaining[:cutoff + 1].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cutoff + 1:].strip()

    return chunks


class ChatBox:
    def __init__(self, ip="127.0.0.1", port=9000):
        self.client = udp_client.SimpleUDPClient(ip, port)
        self._typing = False
        self._lock = threading.Lock()

    def send_message(self, text: str, visible: bool = True):
        """Send a chat message to VRChat (first 144 chars only)."""
        if not text or not text.strip():
            return
        text = text[:VRC_CHATBOX_MAX]
        self._set_typing(False)
        self.client.send_message("/chatbox/input", [text, visible, True])
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

    def stream_text(self, text: str, word_delay: float | None = None):
        """Send text to VRChat word-by-word, trailing slightly behind TTS.

        Each word appears incrementally at a pace calculated to match
        estimated TTS speaking speed (~15 chars/sec), so the chatbox text
        trails a moment behind the spoken audio — like live captions.
        This prevents "spoiling" what the AI is about to say.

        Long text is split into chunks (144 chars max) at sentence boundaries.

        Args:
            text: The text to display.
            word_delay: Seconds between words. If None (default), auto-
                        calculates based on text length ÷ average speech rate
                        so streaming finishes in sync with TTS.
        """
        chunks = _split_into_chunks(text)
        if not chunks:
            return

        # Average English speech: ~15 characters per second
        CHARS_PER_SEC = 15

        self._set_typing(True)
        for idx, chunk in enumerate(chunks):
            is_last = idx == len(chunks) - 1
            words = chunk.split()
            num_words = len(words)
            if num_words == 0:
                continue

            # Auto-calculate word delay to match estimated TTS timing
            delay = word_delay
            if delay is None:
                estimated_speech_sec = len(chunk) / CHARS_PER_SEC
                delay = max(0.15, min(0.8, estimated_speech_sec / num_words))

            # Stream word by word — each message includes all previous words
            for i in range(1, num_words + 1):
                partial = ' '.join(words[:i])
                self.client.send_message(
                    "/chatbox/input", [partial, True, False]
                )
                time.sleep(delay)

            # Brief gap then definitive full text (fixes UDP reordering)
            time.sleep(0.3)
            self.client.send_message(
                "/chatbox/input", [chunk, True, is_last]
            )

            if not is_last:
                time.sleep(0.5)  # Keep typing active between chunks

        time.sleep(0.1)
        self._set_typing(False)
