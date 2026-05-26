"""LLM client using OpenAI-compatible API (works with OpenCode Go).

Handles chat completions with system prompt and conversation history.
"""

import time
from openai import OpenAI


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://opencode.ai/zen/go/v1",
        model: str = "qwen3.6-plus",
        system_prompt: str = "",
    ):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.system_prompt = system_prompt
        self.history = []  # list of {role, content}
        self.on_status = None

    def _status(self, msg: str):
        if self.on_status:
            self.on_status(msg)

    def reset_history(self):
        """Clear conversation history."""
        self.history = []

    def chat(self, user_message: str, max_tokens: int = 300, temperature: float = 0.8) -> str:
        """Send a message and get a response. Blocks until complete."""
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(self.history[-20:])  # keep last 20 messages for context
        messages.append({"role": "user", "content": user_message})

        self._status("Thinking...")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            reply = response.choices[0].message.content or ""
            # Add to history
            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": reply})
            return reply.strip()
        except Exception as e:
            self._status(f"LLM error: {e}")
            return ""

    def chat_stream(self, user_message: str, max_tokens: int = 300, temperature: float = 0.8,
                    on_chunk=None):
        """Stream tokens as they arrive. on_chunk(text) called per token."""
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(self.history[-20:])
        messages.append({"role": "user", "content": user_message})

        self._status("Thinking...")
        full_reply = ""
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    token = delta.content
                    full_reply += token
                    if on_chunk:
                        on_chunk(token)

            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": full_reply})
        except Exception as e:
            self._status(f"LLM stream error: {e}")

        return full_reply
