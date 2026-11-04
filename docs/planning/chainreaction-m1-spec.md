# ChainReaction — Milestone 1 spec: job runner, run store, live events, replay

Scope decision (Nov 1, 18:27): any public Python repo is in scope; the curated presets (nibabel × NumPy 2.0, SponsorFit × Meta API) are the guaranteed-working path. M1 is the foundation every later milestone and the UI build on.

## Run lifecycle
`queued → setup → interpret → investigate → predict → verify → fix → report → done`
Terminal states: `done | failed | cancelled`. Each stage is a function `stage(ctx: RunContext) -> None`.

## RunContext (passed to every stage)
- `ctx.emit(type, data)` — append one event
- `ctx.llm(...)` — the ONLY way stages call the model; records or replays (see below)
- `ctx.run_cmd(cmd, cwd, env_name, timeout)` — the ONLY way stages run subprocesses; records or replays
- `ctx.save_artifact(name, obj_or_text)` — writes under `artifacts/`
- `ctx.cancelled` — checked between steps; subprocesses killed on cancel
- `ctx.mode` — `"live"` or `"replay"`

## Event schema (one JSON object per line in `events.jsonl`)
```
{ "seq": 17, "ts": "2026-11-02T14:03:11.204Z", "run_id": "...", "stage": "verify",
  "type": "stage_start | stage_end | log | tool_call | tool_result | llm_call | cmd | artifact | error",
  "data": { ... } }
```
`seq` is monotonic per run; the SSE stream uses it as the event id.

## Run store layout
```
runs/<run_id>/
  manifest.json      # repo_url, ref, resolved_commit, decision, guide_url, preset, mode,
                     # model, base_url_host, chainreaction_git_sha, created_at, finished_at, status
  events.jsonl
  llm_calls.jsonl    # prompt_hash, model, params (max_tokens, response_format), full system+user,
                     # raw response text, usage tokens, finish_reason, started_at, duration_ms
  cmds.jsonl         # cmd, cwd, env_name, package versions in env, returncode, stdout, stderr, duration_ms
  artifacts/         # changeset.json, graph.json, prediction.json, verify/*.log, fixes/*.diff,
                     # report.json, report.md
```
- `runs/presets/<preset_id>/` is **committed** (judges replay without a key). All other runs are gitignored.
- Never write keys, env var values, or absolute home paths into any run file.

## Replay
- `mode="replay"` re-executes the same stages but `ctx.llm` returns the recorded response by `prompt_hash` and `ctx.run_cmd` returns the recorded result by (cmd, cwd-relative, env_name). No network, no key.
- A missing recording raises `ReplayMiss` and the run fails loudly with the missing hash/command.
- UI badge: "Replayed from run recorded <created_at> on <model>".

## API
- `POST /api/runs` `{repo_url, ref?, decision, guide_url?, preset?, mode?}` → `{id}`
- `GET /api/runs` → list (id, decision, repo, status, created_at, mode)
- `GET /api/runs/{id}` → manifest + latest stage + report (if done)
- `GET /api/runs/{id}/events` → Server-Sent Events; supports `Last-Event-ID` / `?from=<seq>` so the UI can reconnect and history views can re-stream a finished run
- `POST /api/runs/{id}/cancel`
- `GET /api/runs/{id}/artifacts/{name}`

## Execution
- Background worker pool, max 1–2 concurrent runs (env builds are heavy).
- Every subprocess has a timeout; cancel kills the process tree (Windows: `taskkill /T /F`).
- Stage failure → `error` event with message + log tail, status `failed`; earlier artifacts stay.

## Acceptance criteria
1. Live run of the nibabel preset streams events in the browser end to end and produces `report.json`.
2. Replay of that run with `MODEL_API_KEY` unset and network off produces a **byte-identical** `report.json`.
3. Deleting one line from `llm_calls.jsonl` makes replay fail with `ReplayMiss` naming the hash.
4. Cancel during `verify` stops within 5 s and leaves no orphan Python processes.
5. Closing and reopening the browser mid-run resumes the event stream from the last `seq`.
6. Repo scan of `runs/presets/` finds no key, no env var values, no absolute user paths.
7. Existing 60+ unit tests still pass; new tests cover replay hit, replay miss, cancel, and SSE resume.
