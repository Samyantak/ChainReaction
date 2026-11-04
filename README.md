# ChainReaction

Red-line your upgrade before you ship it: type a decision ("upgrade numpy to 2.0"),
ChainReaction predicts what breaks, proves it with real test runs, and proposes a
verified fix.

TODO (M7): 20-second GIF of the domino animation and the fix loop going green.

## Try it in 3 commands (no key needed)

```sh
git clone https://github.com/Samyantak/ChainReaction.git
cd ChainReaction
pip install -e .[dev] && python scripts/fetch_targets.py && python -m chainreaction serve
```

Open http://localhost:8000 and replay any preset from the landing page — no key, no
network. (M7: verify this path on a clean clone in under 10 minutes.)

## What it does

- **Decision:** a plain-English upgrade or vendor switch, parsed into package and versions.
- **Change set:** every breaking change quoted from the migration guide, verified against the source.
- **Investigation:** an agent traces each change with deterministic tools and cites its evidence.
- **Prediction:** static propagation to the package root — HARD and AT RISK sets plus the main chain.
- **Proof:** before/after envs run real probes and suites; predictions score against actuals.
- **Fix:** patches proposed per layer, proven by re-running tests until green.
- **Report:** one checkable claim at a time, each verified, with provenance and exports.

## Results

TODO (M7): per-preset table (chain match, test P/R static vs + agent, file P/R vs PR,
blind recall, fix rounds/status, LLM calls/tokens, wall time) plus ablations
(guide-only vs code-aware; static vs agent; blind vs table recall).

## How grounding works

The model may only assert what a tool returned. Change items carry string-verified
quotes; findings cite tool-call ids; probes and suites run in recorded
environments. Every run is recorded, so any result replays offline with identical
bytes. "Verified" means a deterministic checker confirmed the claim against a
recording — see the report's per-claim checks.

TODO (M7): expand with quote verification, claim checks, and replay details.

## Built with Meta

Muse Spark (`muse-spark-1.3`) via the Meta Model API, plus Muse Code.

TODO (M7): where each is used — structured output (change sets, fixes), tool
calling (investigator), search grounding (migration guide), 1M context
(whole-repo reads), reasoning effort control; Muse Code `/goal` per milestone.

## Live mode setup

```sh
pip install -e .[dev]
```

Set `MODEL_API_KEY` (see `.env.example`; optional `MODEL_API_BASE` /
`MODEL_API_MODEL`). Only live model calls need it — replay needs neither a key
nor network:

```sh
python scripts/fetch_targets.py   # preset checkouts at their pins
python scripts/make_target_envs.py  # version-pinned before/after envs (needs uv)
python scripts/meta_hello.py      # verify the key works
```

## Analyzing your own repo

Post a repo URL + decision to `/api/runs` (or use New analysis in the UI) with
`allow_untrusted: true`.

Supported scope: public Python repos with a pytest suite. Limits: Python +
pytest only; behavior changes are flagged, not predicted; repos needing system
toolchains may not build; monorepos are out of scope.

> **Safety:** analyzing a repo runs its code and tests on this machine. Target
> code never sees `MODEL_API_KEY`, but prefer Docker for repos you have not
> reviewed. Presets are the reviewed path.

## Architecture

TODO (M7): pipeline + run-store diagram and repo layout.

Layout: `src/chainreaction/` (pipeline, stages, API), `ui/` (React app,
`ui/dist` committed so judges need no Node), `runs/presets/` (committed
recordings), `scripts/` (fetch/record/check), `tests/`.

## Limitations

Python + pytest only; behavior changes flagged, not predicted; toolchain-dependent
packages may not build; vendor switches beyond the curated preset are best-effort;
LLM nondeterminism is contained by recording (replay is byte-identical).

## Built during the hackathon

Building period Oct 27 – Nov 4, 2026. TODO (M7): first commit date + commit range.
Pre-existing inputs (analyzed targets, not part of this build): nibabel (MIT),
SponsorFit (author's own), anndata, aiohttp-jinja2, flask-login — all under
`targets/`/`workspaces/` (gitignored, never committed).

## License and acknowledgements

MIT — see [LICENSE](LICENSE). Thanks to the nibabel project (PR #1250), the NumPy
migration guide, and the Meta model cookbook patterns this build adapts.
