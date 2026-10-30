"""Muse Spark client (Meta Model API, OpenAI-compatible).

Single source of truth for the endpoint, model, and key handling.
Key-free paths stay default everywhere; live calls raise a clear
error when MODEL_API_KEY is missing.
"""

import json
import os

KEY_ENV = "MODEL_API_KEY"
BASE_ENV = "MODEL_API_BASE"
MODEL_ENV = "MODEL_API_MODEL"
DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"


def api_key() -> str:
    key = os.environ.get(KEY_ENV)
    if not key:
        raise RuntimeError(
            f"{KEY_ENV} is not set; export it to enable live LLM calls.")
    return key


def base_url() -> str:
    return os.environ.get(BASE_ENV, DEFAULT_BASE_URL)


def model_name() -> str:
    return os.environ.get(MODEL_ENV, DEFAULT_MODEL)


def client():
    from openai import OpenAI

    return OpenAI(base_url=base_url(), api_key=api_key())


def extract_json(text: str):
    """Parse the first {...} or [...] object out of model output."""
    start = min((i for i in (text.find("{"), text.find("["))
                 if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON object in model output")
    opens, closes = ("{", "["), ("}", "]")
    depth, end = 0, -1
    for i in range(start, len(text)):
        ch = text[i]
        if ch in opens:
            depth += 1
        elif ch in closes:
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise ValueError("unterminated JSON object in model output")
    return json.loads(text[start:end])


def chat(system: str, user: str, max_tokens: int = 2000) -> str:
    """One system+user call; returns the message text (raises on failure)."""
    resp = client().chat.completions.create(
        model=model_name(),
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        max_tokens=max_tokens,
    )
    content = resp.choices[0].message.content
    if not content:
        raise RuntimeError("empty response from model")
    return content
