from __future__ import annotations
import os, functools, logging, backoff
from openai import OpenAI, OpenAIError

_API_BASE = "https://openrouter.ai/api/v1"
_HEADERS  = {
    # openrouter requires these two extra headers
    "HTTP-Referer": "https://github.com/smusali/llm-telephone",
    "X-Title": "LLM Telephone (dev)",
}

def _client() -> OpenAI:
    return OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=_API_BASE,
        default_headers=_HEADERS,
        timeout=30,
    )

def _retryable(func):
    """Exponential back-off on rate limits & 5xx."""
    return backoff.on_exception(
        backoff.expo,
        (OpenAIError,),
        max_tries=5,
        jitter=None,
    )(func)

@_retryable
def chat(model: str, messages: list[dict[str, str]]) -> str:
    resp = _client().chat.completions.create(model=model, messages=messages)
    return resp.choices[0].message.content.strip()
