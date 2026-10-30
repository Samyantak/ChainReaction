# Target snapshots (pre-existing inputs, not part of this build)

Snapshots live under `targets/` (gitignored). Pins:

- **nibabel** @ `67ea9972` — per architecture doc eval section.
  - Layer 1 (verified live, numpy 2.5.4): `nibabel/casting.py:717` reads
    `np.sctypes['float']` inside `ok_floats()`, executed at import by line
    723 (`OK_FLOATS = ok_floats()`). Actual chain == predicted spine.
  - Layer 2 (verified live via `--peel` shim of `np.sctypes`):
    `nibabel/quaternions.py:32` `MAX_FLOAT = np.maximum_sctype(float)`
    at module level, reached via
    `imagestats → imageclasses → cifti2 → nifti1 → quaternions`.
- **SponsorFit** @ `af24b10` (`main`, pushed 2026-09-23) — no pin in arch doc;
  this snapshot is the eval baseline until stated otherwise.
  - Verified: `llm.py:14` Provider literal (no `"meta"`); `llm.py:60`
    json_schema Ollama-only `NotImplementedError`; `llm.py:78` runtime
    `ValueError("unknown provider")`; `_call_openai` (`llm.py:156-166`) builds
    `openai.OpenAI()` with no `base_url`; `corpus/extract.py:206-216` calls
    with `provider="ollama"` + `json_schema` + `ollama_options`; `pricing.py`
    `PRICING[provider][model]` KeyErrors on unknown provider/model.

Correction to arch doc: at runtime, `provider="meta"` is rejected by
`llm.py:78` (`ValueError`), not by the line-14 `Literal` (static only).
Demo narration should cite line 78 for the runtime break, line 14 for the
static contract.
