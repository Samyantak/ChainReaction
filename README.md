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

## Generic runs: any public Python repo (M2)

```
POST /api/runs
{"repo_url": "https://github.com/<owner>/<repo>", "ref": "<sha|branch>",
 "decision": "upgrade <pkg> to <ver>", "guide_url": "<optional>",
 "package": "<optional override>", "allow_untrusted": true}
```

The repo is cloned into a cached workspace (`workspaces/`, gitignored),
before/after environments are built under `envs/<workspace-key>/`
(`uv` required), and the run flows through the same stages, recordings,
and replay as presets. Presets stay on their `targets/` path; generic
runs are recorded the same way:

```
python scripts/record_generic.py --repo <url> --decision "<decision>" \
    [--ref R] [--guide URL] [--record-as <preset-id>]
```

> **Unknown repos run untrusted code.** Non-preset runs require
> `allow_untrusted: true`. Target code never sees `MODEL_API_KEY`
> (subprocesses get an allowlisted environment), but prefer Docker for
> repos you have not reviewed. Presets are the reviewed, guaranteed
> path.

### How the after environment is built

The before env resolves the world as of the commit date
(`--exclude-newer`), with the decided package constrained below the
target version. The after env installs the before freeze as
constraints plus the decided `pkg==ver`: pins hold everything else,
and a pin the resolver blames relaxes to a `>=` floor (never
backward, never removed), so cascades move the minimal sufficient
set. Anything else that moves is recorded as a collateral upgrade;
an unresolvable after env is a dependency-conflict finding, not a
crash. Coverage tooling is dropped from the after env (after suites
never measure it, and it can crash collection) unless the repo's own
pytest config needs `--cov`.

### Suites, scoring, replay

Both suites run with the repo's own conftest (the after suite retries
with `--noconftest` only when collection collapses). Predicted
failing tests score against actually-new failures; tests already
failing before the decision never count. A decision on a package the
repo never imports finishes with a warning and skipped suites. Replay
serves every command from the recording (no key, no network) but
needs the source on disk at the recorded commit; artifacts are
byte-identical. A second run of the same repo + decision reuses the
cached workspace and envs (setup ~12 s observed).

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
