from __future__ import annotations

import logging
from pathlib import Path

from google import genai

logger = logging.getLogger(__name__)

_client: genai.Client | None = None
_prompt: str = ""


def init(api_key: str | None, prompt_path: str | Path) -> None:
    global _client, _prompt
    if not api_key:
        logger.info("GEMINI_API_KEY không có — bỏ qua dịch thuật")
        return
    try:
        _client = genai.Client(api_key=api_key)
        _prompt = Path(prompt_path).read_text(encoding="utf-8").strip()
        logger.info("Đã khởi tạo Gemini translator")
    except Exception as exc:
        _client = None
        logger.warning("Khởi tạo Gemini translator thất bại: %s", exc)


async def translate(text: str) -> str:
    if not _client or not text:
        return text
    try:
        resp = await _client.aio.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=text,
            config=genai.types.GenerateContentConfig(
                system_instruction=_prompt,
            ),
        )
        translated = resp.text.strip()
        return translated or text
    except Exception:
        logger.debug("Dịch thuật thất bại, dùng text gốc", exc_info=True)
        return text
