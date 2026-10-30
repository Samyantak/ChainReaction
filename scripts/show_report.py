"""Full key-free pipeline: change set -> prediction -> live verify -> report.

Thin CLI over chainreaction.pipeline (the web backend serves the same).
Default change set is a stub (quoted items recorded by hand); --fetch-guide
fetches the live migration guide and quote-verifies for real.
"""

import sys
from pathlib import Path

from chainreaction import scenarios
from chainreaction.explain import check_report, render_json, render_markdown
from chainreaction.interpret import fetch_text, verify_quotes
from chainreaction.pipeline import run_pipeline


def _opt(prefix: str, default: str | None = None) -> str | None:
    return next((a.split("=", 1)[1] for a in sys.argv if a.startswith(prefix)),
                default)


def main() -> int:
    scenario_id = _opt("--scenario=", "nibabel-numpy2")
    fetch_guide = "--fetch-guide" in sys.argv
    out = _opt("--out=")
    scen = scenarios.get(scenario_id)

    guide_text = None
    if fetch_guide:
        try:
            guide_text = fetch_text(scen.guide_url)
        except Exception as e:  # noqa: BLE001 - report honestly, keep stub
            print(f"guide fetch failed ({e}); using unverified stub")
        else:
            verified, dropped = verify_quotes(list(scen.stub_items), guide_text)
            print(f"guide fetched ({len(guide_text)} chars): {len(verified)} "
                  f"verified, {len(dropped)} dropped — "
                  f"dropped={[i.symbol for i in dropped]}")
    else:
        print("change set source: stub (use --fetch-guide for live verify)")

    res = run_pipeline(scenario_id, fetch_guide=guide_text is not None,
                       guide_text=guide_text)
    g, report = res.graph, res.report
    print(f"change symbols: {report.change_symbols}")
    print(f"layer1 modules={len(report.layers[0].actual)} "
          f"numpy={report.numpy_version}")
    print(f"layer2 (peeled) modules={len(report.layers[1].actual)}")
    print(f"before ok={report.layers[2].ok} numpy={report.before_numpy}")

    violations = check_report(g, report)
    print("CLAIM VIOLATIONS:" if violations else "CLAIMS_CLEAN")
    for v in violations:
        print(f"  {v}")

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
    peel_recall = report.layers[1].recall
    before_ok = report.layers[2].ok
    ok = spine_ok and peel_recall == 1.0 and before_ok and not violations
    print(f"spine_match={spine_ok} peel_recall={peel_recall:.2f} "
          f"before_ok={before_ok}")
    print(f"file P/R vs PR: {report.file_precision:.2f}/"
          f"{report.file_recall:.2f} missed={report.file_missing}")
    print("REPORT_MATCH" if ok else "REPORT_DIFFERS (see above)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
