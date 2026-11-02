"""Muse Spark client (Meta Model API, OpenAI-compatible).

Single source of truth for the endpoint, model, and key handling.
Key-free paths stay default everywhere; live calls raise a clear
error when MODEL_API_KEY is missing.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

KEY_ENV = "MODEL_API_KEY"
BASE_ENV = "MODEL_API_BASE"
MODEL_ENV = "MODEL_API_MODEL"
CACHE_ENV = "LLM_CACHE_DIR"
NO_CACHE_ENV = "LLM_NO_CACHE"
DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
DEFAULT_CACHE_DIR = ".llm_cache"

_memory_cache: dict[str, str] = {}


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


def cache_dir() -> Path | None:
    """Disk cache dir, or None when caching is disabled."""
    if os.environ.get(NO_CACHE_ENV, "") not in ("", "0"):
        return None
    return Path(os.environ.get(CACHE_ENV, DEFAULT_CACHE_DIR))


def cache_key(system: str, user: str, response_format=None) -> str:
    """Stable hash of a prompt (model + messages + schema)."""
    blob = json.dumps({
        "model": model_name(),
        "system": system,
        "user": user,
        "response_format": response_format,
    }, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _read_cache_entry(key: str) -> tuple[str | None, str | None]:
    """Cached reply + entry timestamp (None, None when missing)."""
    if key in _memory_cache:
        return _memory_cache[key], None
    directory = cache_dir()
    if directory is not None:
        path = directory / f"{key}.txt"
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                return None, None
            _memory_cache[key] = text
            try:
                mtime = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc).isoformat()
            except OSError:
                mtime = None
            return text, mtime
    return None, None


def _read_cache(key: str) -> str | None:
    text, _ = _read_cache_entry(key)
    return text


def _write_cache(key: str, content: str) -> None:
    _memory_cache[key] = content
    directory = cache_dir()
    if directory is not None:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{key}.txt").write_text(content, encoding="utf-8")
        except OSError:
            pass  # caching is best-effort; the live reply still stands


def has_key() -> bool:
    """True when live model calls are possible (MODEL_API_KEY is set)."""
    return bool(os.environ.get(KEY_ENV))


def cache_key_for(model: str, system: str, user: str,
                  response_format=None) -> str:
    """Stable hash of a prompt (model + messages + schema)."""
    blob = json.dumps({
        "model": model,
        "system": system,
        "user": user,
        "response_format": response_format,
    }, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class ChatResult:
    content: str
    model: str
    usage: dict | None
    finish_reason: str | None
    cached: bool
    cache_mtime: str | None  # original entry time (disk hits only)


def _usage_dict(resp) -> dict | None:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        try:
            dumped = usage.model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return dumped
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def chat_full(system: str, user: str, max_tokens: int = 8000,
              response_format=None) -> ChatResult:
    """One system+user call with usage + cache metadata (raises on failure).

    Same contract as chat(): repeat prompts are served from cache
    without needing MODEL_API_KEY.
    """
    model = model_name()
    key = cache_key_for(model, system, user, response_format)
    hit, mtime = _read_cache_entry(key)
    if hit is not None:
        return ChatResult(hit, model, None, None, True, mtime)
    kwargs: dict = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
    resp = client().chat.completions.create(**kwargs)
    content = resp.choices[0].message.content
    if not content:
        reason = resp.choices[0].finish_reason
        raise RuntimeError(
            f"empty response from model (finish_reason={reason})")
    _write_cache(key, content)
    return ChatResult(content, model, _usage_dict(resp),
                      resp.choices[0].finish_reason, False, None)


def chat(system: str, user: str, max_tokens: int = 8000,
         response_format=None) -> str:
    """One system+user call; returns the message text (raises on failure).

    Budgets run large because muse-spark reasons first: even a trivial
    reply burns hundreds of reasoning tokens before any text.
    Replies are cached by prompt hash (memory + disk), so reruns are
    free and repeatable — and cache hits don't need MODEL_API_KEY.
    """
    return chat_full(system, user, max_tokens, response_format).content
