ROTATING_MODELS = [
    "google/gemini-2.0-flash", # fast & cheap
    "anthropic/claude-4-sonnet", # good at paraphrasing
]

JUDGE_MODEL = "openai/gpt-4o-mini" # reserved for scoring only
MAX_CYCLES = 30
STOP_SCORE = 2 # ≤ 2 ⇒ terminate
