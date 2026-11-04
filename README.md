# ChainReaction

**Know what breaks before you upgrade.** Type a decision like *"upgrade numpy to 2.0"* for a public Python repo. ChainReaction reads the official migration guide with Muse Spark, traces every affected import through the code, predicts the domino chain, proves it with real before/after test runs, proposes fixes that it keeps only when the tests agree, and writes a report in which every claim is checked.

Every run is recorded, so all five presets below **replay offline with no key and no network**.

[![CI](https://github.com/Samyantak/ChainReaction/actions/workflows/ci.yml/badge.svg)](https://github.com/Samyantak/ChainReaction/actions/workflows/ci.yml)

---

## Why it matters

Upgrades get postponed because nobody can say in advance what they will break. Published studies show the fear is justified:

- **Teams fall behind.** 81.5% of the 4,600+ GitHub projects studied kept outdated dependencies, and developers called updating extra effort and responsibility ([Kula et al., *Empirical Software Engineering*, 2018](https://arxiv.org/abs/1709.04621)).
- **"Safe" releases break too.** 20.1% of non-major releases in Maven Central are breaking ([Ochoa et al., *Empirical Software Engineering*, 2022](https://arxiv.org/abs/2110.07889)). In npm, about 12% of dependent packages and 14% of their releases were hit by a breaking change from a non-major update, and 44% of those breaking changes arrived in minor or patch releases ([Venturini et al., *ACM TOSEM*, 2023](https://arxiv.org/abs/2301.04563)).
- **Python is no exception.** Across six popular Python frameworks, 53.6% of 5,538 client projects use APIs that had breaking changes ([Zhang et al., SANER 2020](https://zhaoxu-zhang.github.io/files/saner2020-preprint.pdf)). That count is static, so it can't say which projects actually fail. ChainReaction answers that by running the tests.
- **Bots open the PR; tests decide.** Over 70% of Dependabot PRs are merged. Developers merge when tests pass and update by hand otherwise, and only 3.4% of PRs carry a compatibility score ([He et al., *IEEE TSE*, 2023](https://arxiv.org/abs/2206.07230)).

ChainReaction is the step before that merge: what breaks, where, why, and a patch the tests agree with.

### Why not just ask a chat assistant?

You can clone a repo into Claude or ChatGPT and ask what an upgrade will break. You'll get a plausible guess, and it can be confidently wrong in two ways ChainReaction is built to catch:

- **It can't see what isn't in the repo.** On flask-wtf, reading the code points at its `url_encode` import. Running it shows the real first break is in the installed Flask 2.2.2, a dependency the repo doesn't contain. ChainReaction ran both environments, found that, and marked its own prediction **DIFFER**.
- **It reports what it noticed, not what's there.** Reading the NumPy 2.0 guide, Muse Spark alone caught 12 of the 65 removals. With deterministic extraction merged in and every item quote-checked, it caught all 65.

ChainReaction uses the same kind of model, but every claim has to come from a tool or a test run: quotes are checked against the source, findings against the tool calls they cite, predictions against real before/after runs, and fixes against the tests. Every run is recorded and replays exactly, so the answer is the same the second time you look.

---

## Quick start

Needs Python 3.11+ and Git. Node is not needed: the built UI is committed.

### 1 · Replay the recorded runs (no key, no network)

**macOS / Linux**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python scripts/fetch_targets.py    # clones the analyzed repos at their pinned commits
python -m chainreaction serve      # then open http://127.0.0.1:8000
```

**Windows (PowerShell)**
```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python scripts/fetch_targets.py
python -m chainreaction serve
```

Pick a drawing on the landing page and press **Replay**. The run streams through all seven sheets exactly as it was recorded: Setup, Interpret, Investigate, Predict, Verify, Fix and Report.

### 2 · Live mode (your own Meta Model API key)

Run this after step 1, in the same virtual environment. Live runs also need [`uv`](https://docs.astral.sh/uv/) to build the before/after environments.

**macOS / Linux**
```bash
pip install uv
export MODEL_API_KEY="<your key>"
python scripts/make_target_envs.py    # once: pinned before/after envs for the presets
python -m chainreaction serve
```

**Windows (PowerShell)**
```powershell
pip install uv
$env:MODEL_API_KEY = "<your key>"
python scripts/make_target_envs.py
python -m chainreaction serve
```

The header then shows **Live mode available**, and each preset gets a **Run live** button. `.env.example` lists every variable; only `MODEL_API_KEY` is required, and only for live model calls. The key stays on the server: it never reaches the browser, and target code never sees it (test subprocesses get an allowlisted environment).

### 3 · Analyze your own repo

Open **New analysis**, enter a GitHub URL, an optional ref, and a decision (`upgrade <package> to <version>` or `upgrade <package> from <A> to <B>`). This path needs `MODEL_API_KEY` (only the presets have recordings to fall back on) and [`uv`](https://docs.astral.sh/uv/) for the environments; without a key the form is disabled.

**Example on a repo it had never seen:** flask-wtf `v1.1.1` with `upgrade werkzeug from 2.2 to 3.0`. Interpret pulled 42 change items from the Werkzeug changelog (all quotes verified) and singled out `werkzeug.urls` as the only one flask-wtf uses. Predict drew a chain to `recaptcha/widgets.py:3` (`url_encode`). Verify then showed the real first break was elsewhere: the after environment kept Flask 2.2.2, which itself imports the removed `werkzeug.urls.url_quote`. ChainReaction reported its own prediction as **DIFFER**, and an investigator finding named the Flask conflict (flagged, because its citations didn't cover every site). Reading flask-wtf's code alone would not show this. That live run is not committed.

**Supported:** public Python repos with a pytest suite; version changes of any PyPI package.
**Not supported (and the UI says so):** non-Python repos, repos that need a system toolchain to build, monorepos, and generic vendor switches (SponsorFit is a curated preset). Behavior-only changes are flagged from the changelog, not predicted.

> **⚠ Analyzing a repo runs its code and tests.** Unknown repos need an explicit confirmation (`allow_untrusted`). Use a container for any repo you haven't reviewed. The presets are reviewed and are the guaranteed path.

---

## What it does

| Sheet | What happens | Muse Spark's part |
|---|---|---|
| **1 · Setup** | Clones the repo at a commit, detects the package and test command, builds pinned *before* and *after* environments with `uv`, runs the baseline suite and a line-level coverage map. | — |
| **2 · Interpret** | Finds the official migration guide or changelog and turns it into a change set. An item survives only if its quote appears in the source word for word. | Search grounding, structured output, 1M-token context |
| **3 · Investigate** | An agent calls ten deterministic tools (find usages, trace importers, callers, propagate, tests covering, read file, import probes, targeted tests, safe snippets) and submits findings. Each finding must cite the tool calls that support it, or it is flagged and kept out of the scores. | Tool calling |
| **4 · Predict** | Builds the AST import graph. Marks modules that break **at import** (HARD) and code that breaks **when called** (AT RISK), and draws the domino chain from the broken line to the package root. | — |
| **5 · Verify** | Runs the real suites before and after; compares predicted and actual chains and failing tests; groups new failures by error signature; peels layers (fix the first break, re-run, find the next). | — |
| **6 · Fix** | Proposes search/replace edits one layer at a time on a throwaway copy, re-runs probes and tests, and keeps an edit only when its target failure clears with nothing new broken. Honesty checks reject skip/xfail markers, deleted tests, silenced exceptions and dependency edits. Download the patch. | Structured output |
| **7 · Report** | Plain-English narrative with a ✓ or ✗ on every claim, a scoreboard against grep and ruff, provenance (model, prompt hashes, timestamps) and exports (Markdown, JSON, zip). | Structured output |

---

## Results

All numbers come from the committed recordings in `runs/presets/`. Replay any of them to check. The analyzed repos are pre-existing projects, not part of this build.

| Preset | Decision | Predicted chain vs real traceback | Test-level P / R | Change set | Fix loop | grep / ruff hits |
|---|---|---|---|---|---|---|
| **nibabel** @ `67ea997` | upgrade numpy to 2.0 | ✓ **MATCH**, 5 hops to `casting.py:717` · 226 HARD | — (import-probe preset) | 12/65 removals by Muse Spark alone → **65/65** with table extraction | PARTIAL: import fixed in round 2, 35 test failures left, 0 newly broken | 315 / 52 |
| **flask-login** @ `31e20a3` | upgrade werkzeug 2.2 → 3.0 | ✓ **MATCH**, 3 hops to `utils.py:14` · 8 HARD | **1.00** / 0.13 (111 new failures) | 42 items from 16 releases | PARTIAL: 5 rounds, 3 failures left, 0 newly broken | 29 / — |
| **aiohttp-jinja2** @ `fd97cef` | upgrade jinja2 to 3.1 | ✓ **MATCH**, 2 hops to `helpers.py:23` · 3 HARD | — (suite has no new failures) | 17 items from 12 releases | **GREEN** in 2 rounds (`@jinja2.contextfunction` → `@jinja2.pass_context`) | 5 / — |
| **anndata** @ `1461fec` | upgrade numpy to 2.0 | no import chain (runtime breaks only) · 17 AT RISK | **1.00 / 0.97** (592 of 612 new failures predicted) | 150 items from NumPy release notes | PARTIAL: 2 rounds (capped), 603 left, 6 newly broken | 87 / 5 |
| **SponsorFit** @ `af24b10` | switch to Meta Model API | ✓ **MATCH**, decision probe at `llm.py:14` | — | code-aware: 3/3 breaks anchored to `file:line` | **GREEN** in 2 rounds | 7 / — |

**File-level, nibabel against the upstream fix (nipy/nibabel PR #1250):** precision 1.00, recall 0.62 (5 of 8 PR files, and 5 of 5 of the files with breaking changes; the other 3 are behavior changes, which ChainReaction flags but does not predict).

### What the comparisons show

| Comparison | Result |
|---|---|
| Change-set recall on the NumPy 2.0 removal table: Muse Spark alone vs with deterministic table extraction | 12/65 → **65/65** |
| SponsorFit vendor switch: guide-only interpreter vs code-aware interpreter (whole repo + guide in one 1M-token call) | 0.0 → **1.0** recall (`llm.py:14`, `llm.py:160`, `pricing.py:23`) |
| Investigator agent vs static prediction, test level | **No improvement on these presets.** flask-login P/R 1.00/0.13 both ways; anndata 1.00/0.97 both ways (the agent did not submit findings in budget, so the static prediction stands). The agent's value here is triage: it ruled out 60 of 69 nibabel change items, 40 of 41 werkzeug items and 16 of 17 jinja2 items as not used, each with cited evidence. |
| Investigator findings that passed verification | nibabel 6/6, aiohttp-jinja2 1/1, SponsorFit 2/3, flask-login 0/1 (flagged, kept out of scores) |

The tables report what the recordings show, including where the agent did not help and where the fix loop stopped short.

---

## How grounding works

**The core rule: the model may only assert what a tool returned.** Deterministic tools produce every fact. Muse Spark reads, chooses where to look and writes, but never supplies a fact on its own.

- **Quote verification.** Every change item carries an exact quote from the guide (or, for vendor switches, an exact span of the repo's source with `file:line`). Items whose quote isn't found word for word are dropped, with the reason recorded.
- **Blind first, then merge.** The model reads the guide without seeing the extracted tables; its items are then merged with deterministic table, list and pattern extraction. That is why the "alone" recall above is meaningful rather than circular.
- **Findings checked against the trace.** Every investigator finding must cite tool calls that exist, every site must appear in a cited result, and "confirmed" needs a matching probe or test. Failing findings are shown flagged and excluded from scores.
- **Claim checks.** Each sentence of the narrative is checked against the run's artifacts (module names, `file:line` citations, counts) and shown with ✓ or ✗.
- **Proof by execution.** "Verified" means the tests ran in both environments. Tests already failing before the decision never count.
- **Recorded runs.** Every model call (prompt, response, parameters, tokens), command, fetch and search is recorded in `runs/<id>/`. Replay serves them back with no network and fails loudly (`ReplayMiss`) on any gap instead of guessing; failed calls are recorded too, so retries replay exactly. Paths are scrubbed so recordings replay on any machine.

---

## Built with Meta

**Muse Spark (`muse-spark-1.3`) via the Meta Model API** (`https://api.meta.ai/v1`, OpenAI-compatible):

| Capability | Where |
|---|---|
| Structured output (`response_format` with a strict JSON schema) | change-set extraction, fix proposals, narrative brief (`interpret.py`, `fixloop.py`, `explain.py`) |
| Search grounding (`web_search` with URL citations) | finding the official changelog or migration guide for a package and version (`sources.py`); the flask-login run cites 13 sources |
| Tool calling (`tool_choice: "auto"`) | the investigator: 10 deterministic tools plus `submit_findings` (`investigate.py`) |
| 1M-token context | the code-aware vendor interpreter reads the whole SponsorFit source and the vendor guide in one call |
| `reasoning_effort: "high"` | investigator turns |

The UI's **Every mark has a source** panel is generated from the recordings themselves (`/api/meta-usage`), so it lists only the capabilities the committed runs actually used.

**Muse Code** built the backend and the first UI. Two sessions ran in parallel (backend on `main`, UI in a separate worktree), each milestone starting as a `/goal` with the spec's acceptance criteria.

---

## Architecture

```
decision → Setup → Interpret → Investigate → Predict → Verify → Fix → Report
             └──────── RunContext: llm · tools · run_cmd · fetch · search · save_artifact · emit ────────┘
                                          │
                 runs/<id>/  manifest.json  events.jsonl  llm_calls.jsonl  cmds.jsonl
                             fetches.jsonl  searches.jsonl  artifacts/
                                          │
                    live: record everything   ·   replay: serve it back (no key, no network)
```

```
src/chainreaction/
  runs/          run context, store, worker, stages, path scrubbing
  graph/         AST import graph
  sources.py     guide / changelog discovery (search grounding, PyPI, releases)
  interpret.py   change set: blind pass, deterministic extraction, merge; code-aware vendor pass
  investigate.py investigator agent: tools, loop, budgets, findings verification
  propagate.py   HARD / AT RISK propagation and chains
  generic.py     any-repo workspaces and pinned before/after envs
  testmap.py     line-level coverage → tests
  verifier.py    suites, failure attribution, layer peeling
  fixloop.py     verified fix loop and honesty checks
  explain.py     narrative brief and claim checks
  web.py, ui_api.py   FastAPI + Server-Sent Events
ui/              React 18 + Vite + Cytoscape (built output committed in ui/dist)
runs/presets/    the five committed recordings
scripts/         fetch_targets, make_target_envs, record_preset, record_generic, check_replay
```

## Development

```bash
pytest                                        # env-dependent tests skip without scripts/make_target_envs.py
python scripts/check_replay.py                # replay every preset offline, byte-compare reports
cd ui && npm ci && npm test && npm run build  # UI tests and rebuild ui/dist
python scripts/record_preset.py <preset-id>   # re-record (needs key, targets, envs)
```

CI runs the tests, the offline replay of every preset, the UI tests, a check that `ui/dist` matches its sources, and a leak scan of the recordings, on Ubuntu, macOS and Windows.

## Limitations

- Python + pytest only.
- Behavior changes (same API, different results) are flagged from the changelog, not predicted; only tests reveal them.
- Fix loops are capped in rounds and time; several presets stop at PARTIAL and say exactly what remains.
- The investigator did not improve test-level scores on these presets (see Results).
- Packages that need a system toolchain may fail environment setup; that is reported as a finding, not a crash.
- Vendor switches are a curated preset (SponsorFit), not yet a general path.
- Model output is nondeterministic; recordings make each run reproducible, but a fresh live run can differ.
- The after environment moves only the decided package. When another dependency is incompatible with the new version but doesn't declare it (Flask 2.2.2 with Werkzeug 3), that dependency becomes the first break; Verify reports it as DIFFER rather than upgrading it for you.
- The honesty checks catch skips, deleted tests, silenced exceptions and dependency edits, but not a fix that patches a third-party module at runtime (seen once on flask-wtf, which still ended PARTIAL).
- The claim checker is conservative: it can flag a number in the narrative that a sheet does show.

## Built during the hackathon

All code in `src/`, `tests/`, `ui/` and `scripts/` was written during the building period, Oct 27 – Nov 4, 2026 (first commit `cd5e064`, Oct 29). The commit history is the evidence. Planning notes are in `docs/planning/`.

The analyzed repos are pre-existing inputs, cloned into `targets/` and `workspaces/` (gitignored) and never committed here: nibabel (MIT), anndata (BSD-3-Clause), flask-login (MIT), aiohttp-jinja2 (Apache-2.0), and SponsorFit (the author's own earlier project).

## License and acknowledgements

MIT — see `LICENSE`.

- nipy/nibabel PR #1250 (NumPy 2.0 support), the ground truth for file-level scoring.
- The NumPy 2.0 migration guide and the Werkzeug, Jinja and NumPy release notes.
- Meta Model API cookbook patterns, adapted for structured output and tool calling.
