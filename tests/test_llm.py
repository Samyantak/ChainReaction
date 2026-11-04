"""Live-LLM surfaces with the network mocked out (offline-safe).

chat() itself is covered by the no-key error path; interpret/
generate_narrative run against canned model replies. (Fix-loop
behavior lives in test_fixloop.py against the M5 search/replace
contract.)
"""

import json

import pytest

from chainreaction import explain, interpret
from chainreaction import llm as llm_mod
from chainreaction.explain import Report
from chainreaction.interpret import ChangeItem
from chainreaction.llm import chat, extract_json

GUIDE = (
    "NumPy 2.0 migration guide. np.sctypes was removed; use explicit "
    "dtype lists instead. The copy keyword behavior changed."
)

MODEL_ITEMS = {
    "items": [
        {"symbol": "numpy.sctypes",
         "kind": "removed",
         "quote": "np.sctypes was removed; use explicit dtype lists instead.",
         "replacement": "explicit dtype lists"},
        {"symbol": "numpy.fake",
         "kind": "removed",
         "quote": "paraphrased words that appear nowhere in the guide"},
    ]
}


def _no_key(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)


def test_extract_json_fenced_and_bare():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('prefix {"a": 2} suffix') == {"a": 2}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_chat_no_key(monkeypatch):
    _no_key(monkeypatch)
    with pytest.raises(RuntimeError, match="MODEL_API_KEY"):
        chat("sys", "hi")


def test_interpret_keeps_verified_drops_paraphrase(monkeypatch):
    seen = {}

    def fake_chat(system, user, **kw):
        seen["system"] = system
        seen["user"] = user
        return json.dumps(MODEL_ITEMS)

    monkeypatch.setattr("chainreaction.interpret.chat", fake_chat)
    cs = interpret.interpret("upgrade numpy", GUIDE, "http://guide")
    assert [i.symbol for i in cs.items] == ["numpy.sctypes"]
    assert cs.items[0].verified is True
    assert "upgrade numpy" in seen["user"]
    assert GUIDE in seen["user"]
    assert "exact" in seen["system"].lower()


def test_interpret_no_key(monkeypatch):
    _no_key(monkeypatch)
    with pytest.raises(RuntimeError, match="MODEL_API_KEY"):
        interpret.interpret("upgrade numpy", GUIDE)


def test_interpret_uses_structured_output(monkeypatch):
    seen = {}

    def fake_chat(system, user, **kw):
        seen.update(kw)
        return json.dumps(MODEL_ITEMS)

    monkeypatch.setattr("chainreaction.interpret.chat", fake_chat)
    interpret.interpret("upgrade numpy", GUIDE)
    assert seen["response_format"] == {
        "type": "json_schema",
        "json_schema": interpret.CHANGE_SET_JSON_SCHEMA,
    }


def test_chat_caches_by_prompt_hash(monkeypatch, tmp_path):
    from chainreaction import llm

    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    calls = []

    class _Msg:
        content = '{"ok": true}'

    class _Choice:
        message = _Msg()
        finish_reason = "stop"

    class _Resp:
        choices = [_Choice()]

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    calls.append(kw)
                    return _Resp()

    monkeypatch.setattr(llm, "client", lambda: _Client())
    first = llm.chat("sys", "hello")
    second = llm.chat("sys", "hello")
    third = llm.chat("sys", "different")
    assert first == second == '{"ok": true}'
    assert third == '{"ok": true}'
    assert len(calls) == 2  # repeat prompt served from cache
    assert any("response_format" in c for c in calls) is False
    cached = list((tmp_path / "cache").glob("*"))
    assert len(cached) == 2  # one entry per distinct prompt hash


def test_chat_full_skips_cache_for_truncated_replies(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from chainreaction import llm

    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    calls = []
    scripts = iter([('{"truncated": tru', "length"),
                    ('{"ok": true}', "stop")])

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    content, reason = next(scripts)
                    calls.append(kw)
                    choice = SimpleNamespace(
                        message=SimpleNamespace(content=content),
                        finish_reason=reason)
                    return SimpleNamespace(choices=[choice], usage=None)

    monkeypatch.setattr(llm, "client", lambda: _Client())
    truncated = llm.chat_full("sys", "trunc-me")
    assert truncated.content == '{"truncated": tru'
    assert truncated.cached is False
    # The truncated reply was never cached: the retry goes live again.
    complete = llm.chat_full("sys", "trunc-me")
    assert complete.content == '{"ok": true}'
    assert complete.cached is False
    assert len(calls) == 2


def test_reasoning_effort_joins_cache_key_only_when_set():
    fmt = {"type": "json_object"}
    base = llm_mod.cache_key_for("m", "s", "u", fmt)
    assert llm_mod.cache_key_for("m", "s", "u", fmt, None) == base
    assert llm_mod.cache_key("s", "u") == llm_mod.cache_key_for(
        llm_mod.model_name(), "s", "u", None, None)
    high = llm_mod.cache_key_for("m", "s", "u", fmt, "high")
    assert high != base


def test_generate_narrative(monkeypatch):
    seen = {}

    def fake_chat(system, user, **kw):
        seen["system"] = system
        seen["user"] = user
        return "NARRATIVE"

    monkeypatch.setattr("chainreaction.explain.chat", fake_chat)
    report = Report(decision="d", target="t", package="nibabel",
                    numpy_version="2.0.2", change_symbols=["numpy.sctypes"],
                    hard=["nibabel.casting"], at_risk=[],
                    spine=["nibabel.casting"])
    assert explain.generate_narrative(report) == "NARRATIVE"
    assert "nibabel.casting" in seen["user"]
    # De-JSONed brief: prose markers in, raw keys out.
    assert "Worst chain" in seen["user"]
    for key in ("spine_match", "numpy_version", "change_symbols",
                "file_precision", "at_risk", "pr_url", "spine_evidence"):
        assert key not in seen["user"]


def test_change_item_verified_flag_roundtrip():
    item = ChangeItem(symbol="s", kind="k", quote="q")
    assert item.verified is False


TOOLS_FIXTURE = [{"type": "function",
                  "function": {"name": "changes",
                               "description": "List change-set items.",
                               "parameters": {"type": "object",
                                              "properties": {}}}}]


def test_tools_join_cache_key_only_when_set():
    base = llm_mod.cache_key_for("m", "s", "u", None, None)
    assert llm_mod.cache_key_for("m", "s", "u", None, None,
                                 None, None) == base
    with_tools = llm_mod.cache_key_for("m", "s", "u", None, None,
                                       TOOLS_FIXTURE, "auto")
    assert with_tools != base
    # Tool choice alone also distinguishes the call.
    assert llm_mod.cache_key_for("m", "s", "u", None, None,
                                 TOOLS_FIXTURE, None) != with_tools


def test_chat_full_tool_calls_round_trip_through_cache(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    calls = []
    raw_args = '{"item": "numpy.sctypes"}'

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    calls.append(kw)
                    fn = SimpleNamespace(name="usages",
                                         arguments=raw_args)
                    tc = SimpleNamespace(id="call_1", type="function",
                                         function=fn)
                    msg = SimpleNamespace(content="checking",
                                          tool_calls=[tc])
                    choice = SimpleNamespace(message=msg,
                                             finish_reason="tool_calls")
                    return SimpleNamespace(choices=[choice], usage=None)

    monkeypatch.setattr(llm_mod, "client", lambda: _Client())
    first = llm_mod.chat_full("sys", "hello", tools=TOOLS_FIXTURE,
                              tool_choice="auto")
    second = llm_mod.chat_full("sys", "hello", tools=TOOLS_FIXTURE,
                               tool_choice="auto")
    assert first.content == second.content == "checking"
    assert second.cached is True
    assert first.tool_calls == [{"id": "call_1", "name": "usages",
                                 "arguments": {"item": "numpy.sctypes"}}]
    assert second.tool_calls == first.tool_calls
    assert len(calls) == 1  # repeat turn served from the cache envelope
    assert calls[0]["tools"] == TOOLS_FIXTURE
    assert calls[0]["tool_choice"] == "auto"


def test_chat_full_plain_text_cache_entry_replays_without_tools(
        monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    key = llm_mod.cache_key_for("m", "s", "u", None, None,
                                TOOLS_FIXTURE, "auto")
    cache_file = tmp_path / "cache" / f"{key}.txt"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("legacy reply", encoding="utf-8")
    monkeypatch.setattr(llm_mod, "model_name", lambda: "m")
    got = llm_mod.chat_full("s", "u", tools=TOOLS_FIXTURE,
                            tool_choice="auto")
    assert got.content == "legacy reply"
    assert got.tool_calls is None
    assert got.cached is True
