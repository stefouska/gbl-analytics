"""Daily job of the GBL app: read esake.gr, then rebuild the warehouse.   python -m pipeline.run [--full]"""
import json
import sys
import traceback
from datetime import datetime, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from config import SEASONS, CURRENT_SEASON, RAW  # noqa: E402
from pipeline.sources import gbl  # noqa: E402
from pipeline import build  # noqa: E402


def main(full_history: bool = False):
    status = {}

    def step(name, fn, *a):
        try:
            out = fn(*a)
            status[name] = f"ok ({len(out)} rows)"
        except Exception as e:  # keep going, report at the end
            status[name] = f"FAILED: {e}"
            traceback.print_exc()

    RAW.mkdir(parents=True, exist_ok=True)
    for s in SEASONS:              # the current season every day; a season never downloaded, once in full
        if full_history or s == CURRENT_SEASON or not (RAW / f"boxscores_{s}.parquet").exists():
            step(f"gbl_{s}", gbl.fetch, s, full_history)
    for s in SEASONS:              # seasons not read today: fix club names still shown as codes
        sf = RAW / f"schedule_{s}.parquet"
        if f"gbl_{s}" not in status and sf.exists():
            import pandas as pd
            sc = pd.read_parquet(sf)
            if (sc.hometeam.astype(str).str.startswith("Team ").any()
                    or sc.awayteam.astype(str).str.startswith("Team ").any()):
                step(f"gbl_names_{s}", gbl.refresh_names, s)
    if list(RAW.glob("boxscores_*.parquet")):
        step("build", lambda: build.main() or [])
    status["_run_at"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    (RAW / "_status.json").write_text(json.dumps(status, indent=2))
    print(json.dumps(status, indent=2))
    if "FAILED" in status.get("build", ""):
        sys.exit(1)


if __name__ == "__main__":
    main("--full" in sys.argv)
