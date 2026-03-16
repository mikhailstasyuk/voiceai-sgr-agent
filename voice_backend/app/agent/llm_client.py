import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, ValidationError

SYSTEM_PROMPT = """
You are Wendy, a posh woman who is ultra concise and fun to talk to about philosophy and other interesting subjects.
You will only ever output 1-2 sentences at a time, and will never use emojis of any kind.
"""

OPTIONAL_AUDIO_MARKUP_PROMPT = """
Audio Markups: use at most one leading emotion/delivery tag—[happy],
[sad],[angry], [surprised], [fearful],[disgusted], [laughing],
or [whispering]—which applies to the rest of the sentence; if
multiple are given, use only the first. Allow inline non-verbal tags
anywhere: [breathe], [clear_throat], [cough], [laugh], [sigh], [yawn].
Use tags verbatim; do not invent new ones.
"""


class StructuredReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spoken_response: str


class GroqStructuredChat:
    _SCHEMA_NAME = "voice_agent_reply"
    _REPLY_CHUNK_SIZE = 12

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self._current_stream = None

    async def cancel(self):
        """Cancels any in-flight streaming call."""
        s = getattr(self, "_current_stream", None)
        if not s:
            return
        # The openai client's stream object has a `close` method.
        for name in ("aclose", "close", "cancel", "stop"):
            fn = getattr(s, name, None)
            if fn:
                try:
                    result = fn()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass
                break

    async def stream_reply(
        self,
        user_text: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncIterator[str]:
        messages: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        completion = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=False,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": self._SCHEMA_NAME,
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "spoken_response": {
                                "type": "string",
                                "description": "The exact assistant reply intended to be spoken to the user.",
                            }
                        },
                        "required": ["spoken_response"],
                        "additionalProperties": False,
                    },
                },
            },
            top_p=1,
            max_tokens=256,
            temperature=0.2,
            presence_penalty=0,
            frequency_penalty=0,
        )

        self._current_stream = completion
        try:
            reply_text = self._extract_reply_text(completion)
            for chunk in self._chunk_reply(reply_text):
                yield chunk
        finally:
            self._current_stream = None

    def _extract_reply_text(self, completion: Any) -> str:
        if not completion.choices:
            raise ValueError("No completion choices returned by Groq.")

        msg = completion.choices[0].message
        content = msg.content
        if content is None:
            raise ValueError("Groq completion did not include message content.")

        raw_json = self._flatten_content(content)
        try:
            parsed = StructuredReply.model_validate_json(raw_json)
        except ValidationError as exc:
            raise ValueError("Groq completion failed strict schema validation.") from exc
        return parsed.spoken_response.strip()

    def _flatten_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif hasattr(part, "type") and getattr(part, "type") == "text":
                    text_parts.append(getattr(part, "text", ""))
            joined = "".join(text_parts).strip()
            if joined:
                return joined
        if isinstance(content, dict):
            return json.dumps(content)
        raise ValueError("Unexpected Groq response content format.")

    def _chunk_reply(self, text: str) -> list[str]:
        clean_text = text.strip()
        if not clean_text:
            return []
        return [clean_text[i : i + self._REPLY_CHUNK_SIZE] for i in range(0, len(clean_text), self._REPLY_CHUNK_SIZE)]
