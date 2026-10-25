"""Parser + pipeline + the pages on fake esake.gr pages (no internet)."""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
PAGES = ("dashboard", "players", "teams", "season_compare", "game_preview")


def test_gbl_end_to_end(tmp_path, monkeypatch):
    import gbl_fixture as fx
    from pipeline.sources import gbl
    from pipeline import build
    monkeypatch.setattr(gbl, "_get", fx.fake_get)
    monkeypatch.setattr(gbl, "RAW", tmp_path / "raw")
    monkeypatch.setattr(gbl, "DEBUG", tmp_path / "raw" / "_debug")
    gbl.fetch(2026)
    bx = pd.read_parquet(tmp_path / "raw" / "boxscores_2026.parquet")
    assert set(bx.Player_ID) >= {"Team", "Total"} and bx.Gamecode.nunique() >= 2
    wh = tmp_path / "wh.duckdb"
    build.main(wh, tmp_path / "raw", ROOT / "data" / "reference")
    code = f"""
import os, sys, json
sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'app')!r}]
os.environ['WAREHOUSE'] = {str(wh)!r}; os.environ['LEAGUE'] = 'GBL'
from streamlit.testing.v1 import AppTest
out = {{}}
for p in {PAGES!r}:
    at = AppTest.from_file({str(ROOT / 'app' / 'pages')!r} + '/' + p + '.py', default_timeout=120)
    at.run()
    out[p] = [str(e.value) for e in at.exception]
print('RESULT' + json.dumps(out))
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    res = json.loads(r.stdout.split("RESULT")[-1])
    assert all(v == [] for v in res.values()), res
