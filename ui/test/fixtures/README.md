# UI fixtures (examples only)

`investigation.example.json` follows the M4 findings schema
(`chainreaction-m4-spec.md` §4: findings, triage, summary) plus the tool
trace (`calls`, per §9 events) and the §5 verification marking
(`verified: false` + `reason`).

`fix.example.json` follows the M5 outputs (`chainreaction-m5-spec.md`
§7: rounds with failure/edits/rationale/validation/probe/tests/timing,
stop reason, final counts, guards, before-env check, patch path).
Each round embeds `diff_text` so the diff viewer can render in demo
mode; on real runs the viewer fetches the `diff` artifact path instead.

These files develop the Investigate and Fix views until Session A
records real `investigation.json` / `fix.json` artifacts. They render
only in feature-page demo mode, labeled "example" — never on a real
run. Real views fetch artifacts and show an empty/skipped state when
absent.
