# ChainReaction report: switch to Meta Model API

Target: `targets/sponsorfit` (pre-existing, analyzed by ChainReaction) | numpy: n/a (decision probe)

## Change set


Behavior-change flags (excluded from scores):

- `meta.muse_voice.transcribe`

## Prediction

HARD: 0 modules | AT_RISK: 0 nodes

Predicted layer-1 chain:

Chain vs layer-1 actual: DIFFERS

## Verification

### decision (recall 0.00)

Actual: `sponsorfit.llm`
Missed: `sponsorfit.llm`
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

You are considering switching the sponsorfit package to the Meta Model API. The scan found no removed functions and no modules failing, with nothing else marked at risk. The only change noted was a behavior difference in Muse voice transcription.

Because nothing failed, there is no verified chain of damage to report. That empty result does not match the failure you actually saw, so it does not explain what happened.

Confidence in this result is very low. The check of the proposed switch breaks during import, and it missed the known problem area in sponsorfit.llm, catching none of what it should have caught.

Fix verification first. Get the import working and make sure sponsorfit.llm is actually exercised before trusting the switch, and manually test the transcription behavior as the most likely source of trouble.

Claim check: ✓ clean
