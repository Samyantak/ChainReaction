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

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

## Meta API hello-world

```
$env:MODEL_API_KEY = "<key>"
python scripts/meta_hello.py
```

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
python scripts/record_preset.py nibabel-numpy2
```

Replay needs only the target source at the recorded commit; test
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
