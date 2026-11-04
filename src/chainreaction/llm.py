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


def cache_key(system: str, user: str, response_format=None,
              reasoning_effort=None, tools=None,
              tool_choice=None) -> str:
    """Stable hash of a prompt (model + messages + schema + effort + tools)."""
    return cache_key_for(model_name(), system, user, response_format,
                         reasoning_effort, tools, tool_choice)


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
                  response_format=None, reasoning_effort=None, tools=None,
                  tool_choice=None) -> str:
    """Stable hash of a prompt (model + messages + schema + effort + tools).

    reasoning_effort, tools, and tool_choice join the key only when set:
    existing recordings hashed without them must keep matching.
    """
    blob = {"model": model, "system": system, "user": user,
            "response_format": response_format}
    if reasoning_effort is not None:
        blob["reasoning_effort"] = reasoning_effort
    if tools is not None:
        blob["tools"] = tools
    if tool_choice is not None:
        blob["tool_choice"] = tool_choice
    return hashlib.sha256(
        json.dumps(blob, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass
class ChatResult:
    content: str
    model: str
    usage: dict | None
    finish_reason: str | None
    cached: bool
    cache_mtime: str | None  # original entry time (disk hits only)
    # Tool calls requested by the model (None for plain-text turns).
    # Each entry: {"id": str, "name": str, "arguments": dict | str}.
    tool_calls: list | None = None


@dataclass
class WebCitation:
    url: str
    title: str
    span: str  # cited slice of the model reply text


@dataclass
class WebSearchResult:
    text: str  # concatenated model reply text (spans slice into this)
    citations: list  # [WebCitation, ...]
    searches: int  # completed web_search_call items
    model: str
    usage: dict | None
    cached: bool
    cache_mtime: str | None


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


def _normalize_tool_calls(message) -> list:
    """Model tool calls as id/name/arguments dicts (tolerates SDK drift)."""
    calls = []
    for item in getattr(message, "tool_calls", None) or []:
        fn = getattr(item, "function", None)
        raw_args = getattr(fn, "arguments", None) if fn is not None else None
        try:
            args = json.loads(raw_args) if isinstance(raw_args,
                                                      str) else raw_args
        except (ValueError, TypeError):
            args = raw_args
        calls.append({"id": getattr(item, "id", None),
                      "name": getattr(fn, "name", None),
                      "arguments": args})
    return calls


def _split_tool_cache_entry(hit: str) -> tuple[str, list | None]:
    """Cache envelope (content + tool calls) or a legacy plain-text entry."""
    try:
        envelope = json.loads(hit)
    except ValueError:
        return hit, None
    if not isinstance(envelope, dict) or "content" not in envelope:
        return hit, None
    calls = envelope.get("tool_calls")
    content = envelope.get("content") or ""
    return content, calls if calls else None


def chat_full(system: str, user: str, max_tokens: int = 8000,
              response_format=None, reasoning_effort=None, tools=None,
              tool_choice=None) -> ChatResult:
    """One system+user call with usage + cache metadata (raises on failure).

    Same contract as chat(): repeat prompts are served from cache
    without needing MODEL_API_KEY. With tools set, the reply's tool
    calls are returned on the result and the cache entry stores a JSON
    envelope (content + tool calls); legacy plain-text entries still
    replay as content with no tool calls.
    """
    model = model_name()
    key = cache_key_for(model, system, user, response_format,
                        reasoning_effort, tools, tool_choice)
    hit, mtime = _read_cache_entry(key)
    if hit is not None:
        if tools is None:
            return ChatResult(hit, model, None, None, True, mtime)
        content, calls = _split_tool_cache_entry(hit)
        return ChatResult(content, model, None, None, True, mtime,
                          tool_calls=calls)
    kwargs: dict = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    resp = client().chat.completions.create(**kwargs)
    message = resp.choices[0].message
    content = message.content
    calls = _normalize_tool_calls(message) if tools is not None else []
    if not content and not calls:
        reason = resp.choices[0].finish_reason
        raise RuntimeError(
            f"empty response from model (finish_reason={reason})")
    finish_reason = resp.choices[0].finish_reason
    if finish_reason != "length":
        # A truncated reply must never poison the prompt's entry: the
        # cache key excludes max_tokens, so the length retry would
        # re-serve the truncated text instead of going live again.
        if tools is None:
            _write_cache(key, content)
        else:
            _write_cache(key, json.dumps(
                {"content": content or "",
                 "tool_calls": calls or None}, sort_keys=True))
    return ChatResult(content or "", model, _usage_dict(resp),
                      finish_reason, False, None,
                      tool_calls=calls or None)


def _message_texts(output) -> tuple[str, list, int]:
    """Concatenated reply text, raw citations, completed search count.

    Tolerates SDK shape drift: anything unexpected is skipped, never
    fatal — an empty citation list just means discovery falls through
    to the next source step.
    """
    texts: list[str] = []
    citations: list[dict] = []
    searches = 0
    for item in output or []:
        kind = getattr(item, "type", None)
        if kind == "web_search_call":
            if getattr(item, "status", None) in (None, "completed"):
                searches += 1
        elif kind == "message":
            for part in getattr(item, "content", None) or []:
                text = getattr(part, "text", None) or ""
                if not isinstance(text, str) or not text:
                    continue
                texts.append(text)
                for annot in getattr(part, "annotations", None) or []:
                    if getattr(annot, "type", None) != "url_citation":
                        continue
                    url = getattr(annot, "url", None)
                    if not url:
                        continue
                    try:
                        start = int(getattr(annot, "start_index", 0))
                        end = int(getattr(annot, "end_index", 0))
                    except (TypeError, ValueError):
                        start, end = 0, 0
                    span = text[start:end] if 0 <= start < end else ""
                    citations.append({
                        "url": url,
                        "title": getattr(annot, "title", None) or "",
                        "span": span if isinstance(span, str) else "",
                    })
    return "\n\n".join(texts), citations, searches


def _search_payload(result: "WebSearchResult") -> dict:
    return {
        "text": result.text,
        "citations": [c.__dict__ for c in result.citations],
        "searches": result.searches,
        "usage": result.usage,
    }


def web_search(query: str) -> WebSearchResult:
    """One Responses API call with the web_search tool (cached by query).

    Raises when MODEL_API_KEY is missing (like chat) or the call fails.
    Repeat queries are served from cache without needing the key.
    """
    model = model_name()
    key = cache_key_for(model, "web_search:v1", query, None)
    hit, mtime = _read_cache_entry(key)
    if hit is not None:
        payload = json.loads(hit)
        return WebSearchResult(
            payload.get("text", ""),
            [WebCitation(c.get("url", ""), c.get("title", ""),
                         c.get("span", ""))
             for c in payload.get("citations", [])],
            payload.get("searches", 0), model,
            payload.get("usage"), True, mtime)
    resp = client().responses.create(
        model=model, input=query, tools=[{"type": "web_search"}])
    text, citations, searches = _message_texts(resp.output)
    result = WebSearchResult(
        text, [WebCitation(c["url"], c["title"], c["span"])
               for c in citations],
        searches, model, _usage_dict(resp), False, None)
    _write_cache(key, json.dumps(_search_payload(result)))
    return result


def chat(system: str, user: str, max_tokens: int = 8000,
         response_format=None, reasoning_effort=None) -> str:
    """One system+user call; returns the message text (raises on failure).

    Budgets run large because muse-spark reasons first: even a trivial
    reply burns hundreds of reasoning tokens before any text.
    Replies are cached by prompt hash (memory + disk), so reruns are
    free and repeatable — and cache hits don't need MODEL_API_KEY.
    """
    return chat_full(system, user, max_tokens, response_format,
                     reasoning_effort).content
