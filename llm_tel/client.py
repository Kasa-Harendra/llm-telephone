from __future__ import annotations

import backoff

from openai import OpenAI, OpenAIError

from llm_tel.config import OPENROUTER_API_KEY, OPENROUTER_API_BASE, OPENROUTER_HEADERS

def _client() -> OpenAI:
    return OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_API_BASE,
        default_headers=OPENROUTER_HEADERS,
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
def chat(model: str, messages: list[dict[str, str]], temperature: float = 1.0) -> str:
    resp = _client().chat.completions.create(model=model, messages=messages, temperature=temperature)
    return resp.choices[0].message.content.strip()
