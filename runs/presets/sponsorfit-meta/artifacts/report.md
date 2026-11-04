# ChainReaction report: switch to Meta Model API

Target: `targets/sponsorfit` (pre-existing, analyzed by ChainReaction)

## Change set

- `sponsorfit.llm.Provider` (rejected_value: "meta" in Provider -> Literal["anthropic","openai","ollama"]; MeteredLLMClient.call raises ValueError on unknown provider; no meta branch exists; `src/sponsorfit/llm.py:14`)
- `sponsorfit.llm.MeteredLLMClient._call_openai` (missing_param: base_url="https://api.meta.ai/v1" -> openai.OpenAI() with no base_url argument; _call_openai uses default OpenAI endpoint; Meta requires custom base_url; `src/sponsorfit/llm.py:160`)
- `sponsorfit.pricing.PRICING` (missing_key: PRICING["meta"]["muse-spark-1.3"] rates -> PRICING keys ["anthropic","openai"] only; compute_cost_usd KeyErrors on unknown model; preview is free but table lacks entry; `src/sponsorfit/pricing.py:23`)

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

## Fix loop

Status: green (green)

Round 1: probe src/sponsorfit/llm.py:78 -> `src/sponsorfit/llm.py`, `src/sponsorfit/pricing.py` (probe FAILED)
Round 2: probe src/sponsorfit/llm.py:79 -> `src/sponsorfit/llm.py` (probe ok)

Counts: 0 fixed, 0 still failing, 0 newly broken (baseline: 0 failures)

Guards: clean

Patch: `fixes/final.patch`

## Baselines

- grep: 7 hits

## Narrative

The push to switch the sponsorfit package to the Meta Model API breaks in three setup places, with no functions removed. In sponsorfit.llm the provider definition only accepts anthropic, openai and ollama and does not accept meta. Also in sponsorfit.llm the OpenAI client is created with no address argument instead of pointing at https://api.meta.ai/v1. In sponsorfit.pricing the pricing table only has entries for anthropic and openai and lacks rates for muse-spark-1.3 under meta.

Two modules fail and five more are at risk. The single verified worst chain of damage centers on sponsorfit.llm and matches the observed failure, with the metered call path reaching the provider definition inside that same module.

Confidence in that chain is high because a direct probe of the switch fails and covers what was expected.

Fix in order and re-probe after each step, because each fix reveals the next layer. Start with the provider definition in sponsorfit.llm at src/sponsorfit/llm.py:14 to allow meta, then the client construction in sponsorfit.llm at src/sponsorfit/llm.py:160 to point at the Meta address, then the pricing table in sponsorfit.pricing at src/sponsorfit/pricing.py:23, and finally the switch logic at src/sponsorfit/llm.py:78.


Claim check: ✓ clean
