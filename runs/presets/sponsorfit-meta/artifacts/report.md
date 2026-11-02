# ChainReaction report: switch to Meta Model API

Target: `targets/sponsorfit` (pre-existing, analyzed by ChainReaction) | numpy: n/a (decision probe)

## Change set

- `sponsorfit.llm.Provider` (rejected_value: "meta" in Provider -> Literal["anthropic","openai","ollama"])
- `sponsorfit.llm.MeteredLLMClient._call_openai` (missing_param: base_url=https://api.meta.ai/v1 -> openai.OpenAI() with no base_url)
- `sponsorfit.pricing.PRICING` (missing_key: PRICING["meta"]["muse-spark-1.3"] -> KeyError: no meta provider table; derived from probe)

## Prediction

HARD: 0 modules | AT_RISK: 5 nodes

Predicted layer-1 chain:

Chain vs layer-1 actual: DIFFERS

## Verification

### decision (recall 1.00)

Actual: `sponsorfit.llm`
```
  File "<string>", line 8, in <module>
  File "<root>/targets/sponsorfit/src/sponsorfit/llm.py", line 78, in call
    raise ValueError(f"unknown provider: {provider}")
ValueError: unknown provider: meta
```

## Baselines

- grep: 7 hits
- ruff NPY201: n/a hits

## Narrative

You are considering switching to the Meta Model API for targets/sponsorfit. The review found no symbols flagged as broken, with zero modules currently failing, though five more are noted as at risk.

There is no verified chain of damage to report. The worst path examined came back clean with no modules involved, and it differs from the observed failure.

Confidence in this result is limited. The check that tries to import the proposed change fails, so the clean finding cannot be fully confirmed, even though testing history shows recall at 1.00.

Fix the import problem first to get a clear signal, then keep close watch on those five at-risk areas in targets/sponsorfit before proceeding with the switch.

Claim check: ✓ clean
