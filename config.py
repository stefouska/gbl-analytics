"""Central settings of the GBL app (data from esake.gr). Change CURRENT_SEASON once a year."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW = DATA / "raw"
REFERENCE = DATA / "reference"
MANUAL = DATA / "manual"
WAREHOUSE = DATA / "warehouse.duckdb"
SQL_DIR = ROOT / "sql"

CURRENT_SEASON = 2026          # 2026-27
SEASONS = [2024, 2025, 2026]   # 2024-25, 2025-26 and 2026-27
WIN_BONUS = 0.10               # kept for the shared SQL
