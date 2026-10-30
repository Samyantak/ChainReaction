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

## Tests

```
pytest
```

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
