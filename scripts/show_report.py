"""Full key-free pipeline: change set -> prediction -> live verify -> report.

Thin CLI over chainreaction.pipeline (the web backend serves the same).
Default change set is a stub (quoted items recorded by hand); --fetch-guide
fetches the live migration guide and quote-verifies for real.
"""

import sys
from pathlib import Path

from chainreaction import scenarios
from chainreaction.explain import (
    check_report,
    generate_narrative,
    render_json,
    render_markdown,
)
from chainreaction.interpret import fetch_text, verify_quotes
from chainreaction.pipeline import run_pipeline


def _opt(prefix: str, default: str | None = None) -> str | None:
    return next((a.split("=", 1)[1] for a in sys.argv if a.startswith(prefix)),
                default)


def main() -> int:
    scenario_id = _opt("--scenario=", "nibabel-numpy2")
    fetch_guide = "--fetch-guide" in sys.argv
    live_interpret = "--interpret" in sys.argv
    narrate = "--narrate" in sys.argv
    out = _opt("--out=")
    scen = scenarios.get(scenario_id)

    guide_text = None
    if fetch_guide or live_interpret:
        try:
            guide_text = fetch_text(scen.guide_url)
        except Exception as e:  # noqa: BLE001 - report honestly, keep stub
            if live_interpret:
                print(f"interpret needs the live guide; fetch failed: {e}")
                return 2
            print(f"guide fetch failed ({e}); using unverified stub")
        else:
            verified, dropped = verify_quotes(list(scen.stub_items), guide_text)
            print(f"guide fetched ({len(guide_text)} chars): {len(verified)} "
                  f"verified, {len(dropped)} dropped — "
                  f"dropped={[i.symbol for i in dropped]}")
    else:
        print("change set source: stub (use --fetch-guide for live verify)")

    if live_interpret:
        print("change set source: live interpret (Muse Spark + quote verify)")
        try:
            res = run_pipeline(scenario_id, guide_text=guide_text,
                               live_interpret=True)
        except RuntimeError as e:
            print(f"interpret failed: {e}")
            return 2
    else:
        res = run_pipeline(scenario_id, fetch_guide=guide_text is not None,
                           guide_text=guide_text)
    g, report = res.graph, res.report
    primary = report.layers[0]
    peeled = next((l for l in report.layers if l.name.startswith("layer2")),
                  None)
    before = next((l for l in report.layers if l.name.startswith("before")),
                  None)
    print(f"change symbols: {report.change_symbols}")
    print(f"{primary.name} modules={len(primary.actual)} "
          f"numpy={report.numpy_version}")
    if peeled is not None:
        print(f"layer2 (peeled) modules={len(peeled.actual)}")
    if before is not None:
        print(f"before ok={before.ok} numpy={report.before_numpy}")

    violations = check_report(g, report)
    print("CLAIM VIOLATIONS:" if violations else "CLAIMS_CLEAN")
    for v in violations:
        print(f"  {v}")

    if narrate:
        try:
            report.narrative = generate_narrative(report)
        except RuntimeError as e:
            print(f"narrative skipped: {e}")
        else:
            print("--- narrative ---")
            print(report.narrative)

    md = render_markdown(report)
    if out:
        out_md, out_json = Path(out + ".md"), Path(out + ".json")
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(md, encoding="utf-8")
        out_json.write_text(render_json(report), encoding="utf-8")
        print(f"wrote {out_md} + {out_json}")
    else:
        print("--- report ---")
        print(md)

    spine_ok = report.spine_match
    ok = primary.recall == 1.0 and not violations
    if report.probe_kind == "import":
        ok = ok and spine_ok
    summary = f"spine_match={spine_ok} primary_recall={primary.recall:.2f}"
    if peeled is not None:
        ok = ok and peeled.recall == 1.0
        summary += f" peel_recall={peeled.recall:.2f}"
    if before is not None:
        ok = ok and before.ok
        summary += f" before_ok={before.ok}"
    print(summary)
    if report.file_precision is not None:
        print(f"file P/R vs PR: {report.file_precision:.2f}/"
              f"{report.file_recall:.2f} missed={report.file_missing}")
    print("REPORT_MATCH" if ok else "REPORT_DIFFERS (see above)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
