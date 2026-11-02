# ChainReaction

Verifiable upgrade-impact investigator: type a decision ("upgrade numpy to 2.0"),
ChainReaction predicts what breaks, proves it with real test runs, and proposes a
verified fix.

## Built during the hackathon

All code in `src/` and `tests/` was written during the hackathon building period
(Oct 27 – Nov 4, 2026). Commit history is the evidence.

## Target repos (pre-existing, analyzed by ChainReaction)

- **nibabel** (MIT) — analyzed input, not part of this build.
- **SponsorFit** (author's own) — analyzed input, not part of this build.

These live under `targets/` (gitignored) and are never committed here.

## Core rule

The model may only assert what a tool returned. Deterministic tools produce every
fact; Muse Spark drives the investigation. Every claim traces to a `file:line`,
a cited changelog span, or a test result.

## Why vendor switches read the code (ablation)

Dependency upgrades break in the vendor's changelog (NumPy 2.0 lists its
63 removed symbols), so a guide-only interpreter suffices. Vendor switches
break in our code: the provider name our schema rejects, the `base_url`
our client never passes, the pricing entry our table lacks. Meta's docs
can't name those gaps, so a guide-only pass can't find them by design.

For vendor decisions ChainReaction runs a code-aware interpreter: the repo
source (~1,900 lines for SponsorFit, well within Muse Spark's 1M context)
plus the vendor guide, with every config break quoting an exact span of
our source with `file:line`, string-verified like guide quotes. It also
keeps the model honest about naming: the guide-only pass found
`openai.OpenAI.base_url` (dropped as out-of-package) for what the
code-aware pass correctly anchors as
`sponsorfit.llm.MeteredLLMClient._call_openai`.

SponsorFit × "switch to Meta Model API", decision-probe recall:

| Interpreter | Recall | What it found |
|---|---|---|
| Guide-only | 0.0 | 0/1 modules; 3 config items dropped (see `docs/ablation/`) |
| Code-aware | 1.0 | 3/3 breaks: `llm.py:14`, `llm.py:160`, `pricing.py:23` |

A first version of the M4 investigator agent, showing long context and
grounded output.

## Setup

```
python -m venv .venv
.venv\Scripts\activate        # Windows (PowerShell)
source .venv/bin/activate     # Linux / Mac (bash)
pip install -e .[dev]
```

## Meta API hello-world

```
$env:MODEL_API_KEY = "<key>"      # Windows (PowerShell)
export MODEL_API_KEY="<key>"      # Linux / Mac (bash)
python scripts/meta_hello.py
```

See `.env.example` for every variable (only the key is required, and
only for live model calls — replay needs neither a key nor network).

## Target environments (before/after)

The target never runs under this repo's own interpreter. Version-pinned
environments live in `envs/` (needs `uv` once, for Python 3.11):

```
python scripts/make_target_envs.py
```

- `envs/numpy1` — numpy 1.26.4: the "before" run, `import nibabel` passes.
- `envs/numpy2` — numpy 2.0.2: the "after" run, the sctypes break reproduces.
- `envs/sponsorfit` — sponsorfit's deps: the Meta-switch decision probe.

Probes, the pipeline, and the fix-loop demo all resolve these via
`chainreaction.verifier.target_python()`.

## Tests

```
pytest
```

## Runs: live, replay, stream (M1)

Each analysis is a background run with an event stream and an
offline replay. `runs/presets/` holds committed recordings (judges
replay these with no key and no network); all other runs are local.

```
uvicorn chainreaction.web:app --port 8000
```

- `POST /api/runs` `{preset, mode?, replay_from?, decision?, guide_url?}`
- `GET /api/runs/{id}/events` — Server-Sent Events (auto-resumes)
- `POST /api/runs/{id}/cancel`, `GET /api/runs/{id}/artifacts/{name}`
- Bare stream view: `/stream.html?run=<id>` (after `npm run build`)

Record (or re-record) a preset — needs the key, the target, and envs:

```
python scripts/fetch_targets.py               # clone targets at their pins
python scripts/record_preset.py nibabel-numpy2
```

Replay needs only the target source at the recorded commit — fetch it
with `scripts/fetch_targets.py`, which verifies the pin — and test
commands come back from the recording, so no env builds are needed.

## UI skeleton (React + FastAPI)

Backend (from the repo root, `.venv` active):

```
pip install -e ".[dev]"
uvicorn chainreaction.web:app --port 8000
```

Frontend (needs Node 18+; in another shell):

```
cd ui
npm install
npm run dev
```

Open http://localhost:5173 in a browser (Vite proxies `/api` to FastAPI).
`npm run build` produces `ui/dist`, which the backend serves at `/` when
present.
