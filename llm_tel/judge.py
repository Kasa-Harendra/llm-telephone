import json, statistics, logging
from .client import chat
from .prompts import JUDGE_SYS, JUDGE_USER_TMPL
from .config import JUDGE_MODEL

def score(original: str, transformed: str, n: int = 1) -> float:
    msgs = [
        {"role": "system", "content": JUDGE_SYS},
        {"role": "user",   "content": JUDGE_USER_TMPL.render(
            original=original, transformed=transformed)},
    ]
    scores = []
    for _ in range(n):
        reply = chat(JUDGE_MODEL, msgs, temperature=0)
        try:
            scores.append(json.loads(reply)["score"])
        except Exception as exc:
            logging.warning("Judge parse error → %s", exc, exc_info=True)
    return statistics.mean(scores)
