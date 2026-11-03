# UI event map (M6 §12)

Every SSE event from `GET /api/runs/{id}/events` and the component that
renders it. Envelope (see `RunContext.emit`):

```json
{ "seq": 12, "ts": "2026-11-02T05:15:54Z", "run_id": "…",
  "stage": "interpret", "type": "log", "data": { … } }
```

`stage` is the emitting stage; views filter the log by their stage.
`EventSource` resumes with `Last-Event-ID` after a drop; the server
replays missed events, then closes the stream once the manifest is
terminal (the client stops reconnecting when the snapshot is terminal).

| type | data | produced by | consumed by |
|---|---|---|---|
| `stage_start` | `{stage}` | worker, per stage | stage rail (→ running + start time) |
| `stage_end` | `{stage}` | worker, per stage | stage rail (→ done); refetch run snapshot |
| `log` | `{message}` or `{warning}` | all stages; replay warnings at end | stage log rows; terminal state shows tail |
| `error` | `{stage, error, traceback?}` | worker on stage exception | stage rail (→ failed); failed-state panel |
| `fetch` | `{url, status, bytes, table_rows?}` | `ctx.fetch` | interpret log row (guide fetch) |
| `llm_call` | `{prompt_hash, model?, cached?}` | `ctx.llm` | interpret/investigate/report log rows |
| `cmd` | `{cmd, cwd, env_name, returncode, timed_out}` | `ctx.run_cmd` | setup/verify/fix log rows (probes, suites) |
| `artifact` | `{name}` | `ctx.save_artifact` | artifact links in the owning stage's view |

Reserved (M4/M5, not emitted yet — render as generic rows until their
views land):

| type | data | future consumer |
|---|---|---|
| `tool_call` | `{id, name, args}` | investigate tool log |
| `tool_result` | `{id, summary, ok, truncated}` | investigate tool log |

Unknown `type` values render as a generic log row (`{type}: {JSON}`)
and never crash the view.
