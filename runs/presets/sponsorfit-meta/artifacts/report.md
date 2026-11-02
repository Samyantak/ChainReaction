# ChainReaction report: switch to Meta Model API

Target: `targets/sponsorfit` (pre-existing, analyzed by ChainReaction)

## Change set

- `sponsorfit.llm.Provider` (rejected_value: "meta" in Provider -> Literal["anthropic","openai","ollama"]; Meta Model API needs a distinct provider; call() raises ValueError unknown provider for anything outside the Literal; `src/sponsorfit/llm.py:14`)
- `sponsorfit.llm.MeteredLLMClient._call_openai` (missing_param: OpenAI client constructed with base_url https://api.meta.ai/v1 -> openai.OpenAI() with no base_url argument; Meta is OpenAI-compatible only via base_url override; client never passes base_url; `src/sponsorfit/llm.py:160`)
- `sponsorfit.pricing.PRICING` (missing_key: PRICING contains muse-spark-1.3 rates -> PRICING keys are anthropic/openai only (claude-*, gpt-4o*); compute_cost_usd fails loud on unknown model; preview free still needs explicit entry; `src/sponsorfit/pricing.py:23`)

## Prediction

HARD: 2 modules | AT_RISK: 5 nodes

Predicted call chain:

- `sponsorfit.llm.MeteredLLMClient.call` -> `sponsorfit.llm` (src/sponsorfit/llm.py:14)
Chain vs decision actual: MATCH

## Verification

### decision (recall 1.00)

Actual: `sponsorfit.llm`
Site: `src/sponsorfit/llm.py:78`
```
  File "<string>", line 8, in <module>
  File "<root>/targets/sponsorfit/src/sponsorfit/llm.py", line 78, in call
    raise ValueError(f"unknown provider: {provider}")
ValueError: unknown provider: meta
```

## Baselines

- grep: 7 hits

## Narrative

The plan to switch to the Meta Model API breaks the sponsorfit package in three places. Nothing is removed, but the code still assumes the old setup.

In sponsorfit.llm the provider definition only permits anthropic, openai and ollama, so meta is not accepted. Also in sponsorfit.llm the client for OpenAI calls is created without an address, while it now must point at https://api.meta.ai/v1. In sponsorfit.pricing the pricing table covers only anthropic and openai families such as claude and gpt-4o types, with no rates for muse-spark-1.3.

Two modules fail with five more at risk. The single verified worst chain centers on sponsorfit.llm and matches the observed failure, since the call through MeteredLLMClient in sponsorfit.llm reaches the provider definition at src/sponsorfit/llm.py:14. Evidence is strong because a direct probe fails.

Fix first the provider definition in sponsorfit.llm at src/sponsorfit/llm.py:14, then the OpenAI call helper in sponsorfit.llm at src/sponsorfit/llm.py:160, then the pricing table in sponsorfit.pricing at src/sponsorfit/pricing.py:23, and finally the decision site at src/sponsorfit/llm.py:78. Work in that order and re-probe after each step, as each fix reveals the next layer.


Claim check: ✓ clean
