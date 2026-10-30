# ChainReaction — architecture (v3, 2026-10-26, shaped to the judging rubric)

Core rule: **the model may only assert what a tool returned.** Muse Spark drives the investigation; deterministic tools produce every fact. Every claim shown to a judge traces to a file:line, a cited changelog span, or a test result.

## Judging rubric → strategy
| Criterion | Weight | Our play |
|---|---|---|
| Impact | 30% | Upgrades and vendor switches are decisions every software team fears and postpones. Product shape: run before you merge a Dependabot/Renovate bump or switch an API provider — "what breaks, why, and here's the verified fix." Fits *Push the Frontier* (software). Back the pitch with real figures on upgrade breakage (to research — no invented numbers). |
| Use of Meta AI Tooling | 25% | Go beyond "LLM explains": Muse Spark is an **investigator agent** (tool calling) over verified tools; **search grounding** finds and cites the migration guide itself; **structured output** for change sets; **1M context** powers the whole-repo baseline and full-guide reading; **fix loop** proposes patches the verifier proves; optional **open-weights privacy mode** (Muse Spark Open / Glimmer) so proprietary code never leaves your infra. |
| Technical Depth & Execution | 20% | AST dependency graph with import-time vs call-time edges, propagation rules, layer peeling, real backtest with precision/recall vs three baselines. Already our strongest area — don't over-invest here at the expense of the 55% above. |
| Demo | 25% | Visual cascade (dominoes fall across the graph), then the real test run turns the graph red, scoreboard shows predicted vs actual, then the fix lands and it turns green. Live product holds up: cached scenarios load instantly, a live re-run runs in a Modal sandbox. |

## Rules compliance (from the Participant Guide)
- **New work only (Indie track):** everything presented must be built during the event. → Fresh public repo, first commit on/after Oct 27 (building period Oct 27 – Nov 4). No code reused from SponsorFit or any prior project.
- **Demo must only highlight what was built during the hackathon.** SponsorFit and nibabel are *inputs*: label them in the UI and video as "target repo (pre-existing, analyzed by ChainReaction)". README gets a "Built during the hackathon" section; commit history is the evidence.
- **Repo must be public.** **Rights:** nibabel is MIT; SponsorFit is your own; Meta's cookbook is MIT (patterns may be adapted with attribution); dependencies are standard OSS.
- **Stage One (pass/fail):** must reasonably use Meta Model API and/or open weights → Meta models drive the agent, interpreter, explainer, baseline, and fix loop.
- **Submission:** public repo + **3-minute demo video** + **live product**. Due Nov 4, 9:00 PM ET via the CV platform.
- Not an anti-project.

## Meta Model API facts (from meta-models/meta-model-cookbook)
- OpenAI-SDK compatible: `base_url="https://api.meta.ai/v1"`, key in `MODEL_API_KEY` (pass explicitly). Also Anthropic-SDK compatible.
- Default model `muse-spark-1.3`, 1,048,576-token context.
- Structured output: `response_format={"type":"json_schema",...}`, constrained decoding.
- Tool calling supported; `tool_choice` only supports `"auto"`.
- Search grounding: Responses API with `tools=[{"type":"web_search"}]`; returns `url_citation` annotations mapping exact answer characters to source URLs.
- Also: prompt caching, reasoning-effort control, vision.
- Open weights: Muse Spark Open on Baseten as `meta-models/muse-spark-open-1026`; Muse Glimmer (small, local).

## Pipeline
```
 decision text ──► [2] Decision Interpreter (Muse Spark + search grounding + structured output)
                        │   finds & cites the changelog/migration guide → Change Set (each item quote-verified)
 repo @ commit ──► [1] Graph Builder (AST, no LLM) ──► System Graph
                        │
                   [3] Investigator Agent (Muse Spark, tool calling) — plans and runs the investigation:
                        tools: find_usages · trace_importers · propagate · run_tests · apply_shim · read_file
                        │   (every tool is deterministic; the agent chooses what to check next)
                   [4] Propagation Engine (rules)      [5] Verifier (sandbox: apply decision, run tests, peel layers)
                        │
                   [6] Explainer + Claim Checker (every claim must match a tool result)
                        │
                   [7] Fix loop: model proposes patch → verifier re-runs → green
                        │
                   [8] UI + Scoreboard
```

## Components
1. **Graph Builder** (Python `ast`, NetworkX). Nodes: module, function/class, external symbol (`numpy.sctypes`), package (lockfiles), vendor (SDK imports, env vars, provider strings), test. Edges: `IMPORTS` (tagged import-time vs inside-function), `CALLS`, `USES_EXTERNAL`, `EXECUTES_AT_IMPORT`, `TESTED_BY`, `CONFIGURES`. Every node/edge stores file:line evidence. Resolves aliases; detects guards.
2. **Decision Interpreter.** User types only the decision ("upgrade numpy to 2.0"). Muse Spark uses search grounding to find the official migration guide/changelog, then emits a schema-constrained change set. Grounding: each item's quote must string-match the fetched source text (citations give the URL; we fetch and verify), or it is dropped.
3. **Investigator Agent** (Muse Spark, tool calling). Seeds from the change set, then decides which usages to trace, which tests to run, which failure to shim to reveal the next layer. All tools are deterministic; the trace of tool calls is shown in the UI as the "investigation log."
4. **Propagation Engine** (deterministic rules): import-time break → module and all importers fail (HARD); in-function break → callers AT_RISK; guards downgrade; full chain kept per node.
5. **Verifier** (local venv → Modal sandbox). Apply the decision for real, run tests, parse tracebacks into actual chains. **Layer peeling:** shim the first failure (e.g., restore `np.sctypes`) and re-run to reveal and score the next layer.
6. **Explainer + Claim Checker.** Narrative + mitigation plan; every path, line, symbol, and count checked against tool results; unsupported claims flagged or stripped.
7. **Fix loop (core).** Model proposes a patch for the first-layer break → verifier re-runs → tests pass again. Muse Code for development and, where it fits, patch generation.
8. **UI + Scoreboard.** FastAPI + React + Cytoscape.js; dominoes fall outward; click any node for its evidence; predicted vs actual; investigation log; target repo clearly labeled as pre-existing.
9. **Stretch:** open-weights privacy mode (same agent on Muse Spark Open via Baseten / Glimmer local); PR-comment output for Dependabot/Renovate bumps (Meta cookbook has a GitHub Actions repo-agent recipe to adapt).

## Evaluation
- **nibabel × NumPy 2.0** (base `67ea9972`): (a) chain match vs actual traceback `casting.py:717 → volumeutils → arrayproxy → analyze → import`; (b) file-level precision/recall vs the 30 files PR #1250 touched and the 19 removed-API usages; (c) next-layer predictions scored via layer peeling.
- **Baselines:** grep; ruff `NPY201`; Muse Spark alone with the whole repo in its 1M context. Claim: ≥ ruff on locations, uniquely gets the cascade, beats model-alone on precision — i.e., the same model gets better when it must prove its claims.
- **SponsorFit × "switch to Meta Model API"**: two ways to apply it, two cascades:
  - new `provider="meta"`: `llm.py:14` Provider literal rejects it → `llm.py:60` json_schema is Ollama-only → `extract.py:207-215` breaks; `pricing.py` KeyError
  - `provider="openai"` with Meta's base_url: literal passes, but `_call_openai` has no base_url config, json_schema still Ollama-only, `pricing.py` KeyErrors on `muse-spark-1.3`

## Demo video (3 min) — draft arc
1. 0:00–0:20 Hook: "Every team postpones upgrades because nobody knows what breaks." Type "upgrade numpy to 2.0" on nibabel.
2. 0:20–1:10 Agent finds and cites the migration guide; investigation log streams; dominoes fall from `casting.py:717` to the whole package.
3. 1:10–1:50 Proof: real tests run in the sandbox, graph turns red, scoreboard: predicted vs actual, vs ruff and model-alone.
4. 1:50–2:30 Fix loop: patch proposed, tests re-run, green.
5. 2:30–2:55 Second decision on SponsorFit: "switch to Meta Model API" — two ways, two cascades.
6. 2:55–3:00 What was built during the hackathon + live link.

## Build plan (building period Oct 27 – Nov 4)
| Date | Work |
|---|---|
| Mon Oct 26 | Claim credits (Meta $150, Modal, Baseten, Fireworks); Meta API hello-world. No product code yet |
| Tue Oct 27 | Public repo (first commit); graph builder skeleton. Workshop: Agents 101 (12 PM) |
| Wed Oct 28 | Graph builder on nibabel; reproduce import chain from graph alone. **Workshop: Verifiable Agents (12 PM)** |
| Thu Oct 29 | Propagation + verifier + scorer; grep/ruff baselines |
| Fri Oct 30 | Decision interpreter (search grounding + structured output + quote check); investigator agent loop. Fireworks workshop (12 PM) |
| Sat Oct 31 | Explainer + claim checker; model-alone 1M-context baseline |
| Sun Nov 1 | Fix loop; SponsorFit scenario |
| Mon Nov 2 | UI + scoreboard + investigation log |
| Tue Nov 3 | Deploy live product (Modal); dry-run the demo. Last day for organizer troubleshooting |
| Wed Nov 4 | Record 3-min video, README, submit by afternoon (hard deadline 9 PM ET) |

Office hours: Discord #office-hours, weekdays 10–11 AM ET.

## Open items
- Research real figures for the impact pitch (upgrade breakage / NumPy 2.0 ecosystem fallout)
- Credits are batched and need a card on file — claim early
