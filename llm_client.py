"""LLM client using OpenAI-compatible API (works with OpenCode Go).

Handles chat completions with system prompt and conversation history.
"""

from openai import OpenAI


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://opencode.ai/zen/go/v1",
        model: str = "mimo-v2.5-pro",
        system_prompt: str = "",
        max_tokens: int = 150,
        max_history: int = 5,
    ):
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0)
        self.model = model
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.max_history = max_history
        self.history = []  # list of {role, content}
        self.on_status = None

    def _status(self, msg: str):
        if self.on_status:
            self.on_status(msg)

    def reset_history(self):
        """Clear conversation history."""
        self.history = []

    def chat(self, user_message: str, max_tokens: int = None, temperature: float = 0.8) -> str:
        """Send a message and get a response. Blocks until complete."""
        if max_tokens is None:
            max_tokens = self.max_tokens
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if self.max_history > 0:
            messages.extend(self.history[-self.max_history:])
        else:
            messages.extend(self.history)  # 0 = unlimited
        messages.append({"role": "user", "content": user_message})

        self._status("Thinking...")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            choice = response.choices[0]
            message = choice.message
            reply = message.content if message.content is not None else ""
            # Add to history
            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": reply})
            return reply.strip()
        except Exception as e:
            self._status(f"LLM error: {e}")
            return ""

    def chat_with_image(self, user_message: str, image_bytes: bytes,
                        temperature: float = 0.8) -> str:
        """Send text + image to the model (single model, full context)."""
        import base64
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_uri = f"data:image/png;base64,{b64}"

        # Prompt: game content only — no UI, no usernames, no HUD
        text_block = user_message + "\n\nSTRICT RULES FOR THIS IMAGE:\n"
        text_block += "- Do NOT mention any text, usernames, player names, labels, or on-screen words.\n"
        "- Do NOT mention FPS counters, icons, buttons, menus, HUD elements, or interface panels.\n"
        "- Do NOT mention microphone icons, speaker icons, mute/unmute indicators, or settings buttons.\n"
        "- Describe ONLY the 3D scene, environment, lighting, avatars, and what characters are doing.\n"
        "- If you see text, ignore it completely. Act like it is not there.\n"
        "- Treat the image like a photograph of a real place, not a video game screenshot."

        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if self.max_history > 0:
            messages.extend(self.history[-self.max_history:])
        else:
            messages.extend(self.history)
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": text_block},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]
        })

        self._status("Looking...")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=temperature,
            )
            reply = response.choices[0].message.content or ""
            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": reply})
            return reply.strip()
        except Exception as e:
            self._status(f"Vision error: {e}")
            return ""

    def chat_stream(self, user_message: str, max_tokens: int = 300, temperature: float = 0.8,
                    on_chunk=None):
        """Stream tokens as they arrive. on_chunk(text) called per token."""
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if self.max_history > 0:
            messages.extend(self.history[-self.max_history:])
        else:
            messages.extend(self.history)  # 0 = unlimited
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
