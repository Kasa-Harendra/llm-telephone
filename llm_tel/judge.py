import json
import statistics
import logging

from llm_tel.client import chat
from llm_tel.prompts import JUDGE_SYS, JUDGE_USER_TMPL
from llm_tel.config import JUDGE_MODEL

def score(original: str, transformed: str, n: int = 1) -> float:
    msgs = [
        {"role": "system", "content": JUDGE_SYS},
        {"role": "user",   "content": JUDGE_USER_TMPL.render(original=original, transformed=transformed)},
    ]

    scores = []
    for _ in range(n):
        reply = chat(JUDGE_MODEL, msgs, temperature=0)
        try:
            if reply.strip():  # Check if reply is not empty
                scores.append(json.loads(reply)["score"])
            else:
                logging.warning("Judge returned empty response, using fallback score")
                scores.append(1)  # Fallback score for empty response
        except Exception as exc:
            logging.warning(f"Judge parse error → {exc}, using fallback score")
            scores.append(1)  # Fallback score for parse errors

    return statistics.mean(scores) if scores else 1.0  # Return 1.0 if no scores
