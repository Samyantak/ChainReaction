# ChainReaction v1 — full product plan (2026-11-01, 18:22)

Decision (Samyantak, Nov 1 18:22): ship a **full, working product with a complete UI**, not a judge-minimum build. Working overtime. Open-weights bonus skipped. Deadline: Nov 4, 9:00 PM ET.

## What "full-fledged" means (v1 scope)
- **Any public Python repo** (GitHub URL + optional ref) with a pytest suite, not just canned scenarios. nibabel and SponsorFit become one-click presets.
- **Decisions supported:** (a) dependency version change for any PyPI package ("upgrade numpy to 2.0"); (b) vendor/provider switch (curated SponsorFit preset; generalized only if time allows).
- **Out of scope, stated in the UI:** non-Python repos, repos needing system toolchains to build, monorepos, behavior-only changes (flagged from the changelog, not predicted).
- **Safety:** analyzing a repo runs its code and tests. Clear warning before running an unknown repo; recommend Docker for untrusted repos.

## User flow (UI)
1. **New analysis:** repo URL + decision text, or a preset card (nibabel × NumPy 2.0, SponsorFit × Meta API).
2. **Setup (streamed):** clone at ref → detect package + test command → build "before" and "after" envs with uv → baseline test run + coverage map. Cached by repo commit + dependency set.
3. **Interpret:** Muse Spark finds the official migration guide (search grounding with citations; fallback: user-supplied guide URL) → change set via structured output → each item shown as quote-verified ✓ or dropped ✗, linked to the source.
4. **Investigate:** investigator agent (tool calling: find_usages, trace_importers, propagate, tests_covering, run_probe, read_file) with a live log.
5. **Prediction:** graph with domino reveal from the broken line to the package root; collapsed "+N more" clusters; click a node for file:line + code snippet.
6. **Verify:** before vs after test results; predicted vs actual failing tests; layer peeling.
7. **Fix:** Muse Spark proposes a patch per layer → diff viewer → apply → re-run → counts; loop to green (max 3) → download patch.
8. **Report:** plain-English narrative with per-claim ✓/✗, scoreboard (chain match, file P/R, vs grep/ruff), provenance (model, timestamps, prompt hashes), export Markdown/JSON.
9. **History:** past runs list; open any run in replay mode (no network, no key).

## Backend architecture additions
- **Job runner:** each analysis is a background job; `POST /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/events` (Server-Sent Events), cancel endpoint, timeouts.
- **Run store = replay + provenance:** `runs/<id>/` with `manifest.json`, `events.jsonl`, `llm_calls.jsonl` (model, params, full prompt, raw response, tokens, finish_reason, timestamp), verifier logs (env, package versions, command, output), fix diffs, report. Replay re-streams events from disk; a missing LLM recording fails loudly. Preset runs are committed so judges can replay without a key.
- **Workspace manager:** clone any repo at ref, detect package (pyproject name / top-level package dirs) and test command, create before/after envs with uv, cache envs.
- Scenarios become presets on top of the generic path.

## Milestones (keep main always demoable)
1. **Job runner + run store + SSE + replay** — foundation for UI and provenance.
2. **Generic workspace** — any repo + any package upgrade; nibabel re-runs through it with identical results.
3. **Live interpreter end to end** — search grounding / guide URL, structured output, quote verification surfaced.
4. **Investigator agent** — tool calling over existing deterministic functions.
5. **Live fix loop** — Muse Spark patches per layer, verified by real test runs.
6. **Full UI** — all nine views above.
7. **Validation on 2 more real breakages** (repos with documented upgrade-fix PRs) + README (cross-platform, `.env.example` with `MODEL_API_KEY`, fetch/setup scripts) + 3-minute video.

Feature freeze: Wed Nov 4, ~2 PM ET, then only fixes, README, and video.

## Organizer guidance still applies
Judges run the demo from the public README with their own keys; replay mode with full provenance is an accepted path; keys never in the repo or the client; no hosted version required.
