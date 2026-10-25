"""Rebuild data/warehouse.duckdb from data/raw + data/reference + sql/*.sql"""
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import RAW, REFERENCE, WAREHOUSE, SQL_DIR, WIN_BONUS  # noqa: E402

# Empty schemas used when a source has never been fetched (e.g. first run offline)
EMPTY = {
    "raw_schedule": "season INT, gameday INT, round VARCHAR, date VARCHAR, startime VARCHAR, gamecode VARCHAR, "
                    "hometeam VARCHAR, homecode VARCHAR, hometv VARCHAR, awayteam VARCHAR, awaycode VARCHAR, "
                    "awaytv VARCHAR, played VARCHAR",
    "raw_standings": "season INT, round INT",
    "raw_players": "season INT, player_id VARCHAR, player_name VARCHAR, club_code VARCHAR, position_api VARCHAR, "
                   "dorsal VARCHAR, nationality VARCHAR, height_cm DOUBLE, birth_date VARCHAR, active BOOLEAN",
    "raw_prices": "full_name VARCHAR, prices_team VARCHAR, price DOUBLE, price_key VARCHAR, season INT, "
                  "round INT, scraped_at TIMESTAMP",
    "raw_injuries": "player VARCHAR, team VARCHAR, position_site VARCHAR, status VARCHAR, round_info VARCHAR, "
                    "comments VARCHAR, source VARCHAR, scraped_at TIMESTAMP",
    "raw_fantasy_league": "season INT, round INT, fantasy_team VARCHAR, points DOUBLE, clan VARCHAR",
    "raw_injuries_history": "player VARCHAR, team VARCHAR, position_site VARCHAR, status VARCHAR, "
                            "round_info VARCHAR, comments VARCHAR, source VARCHAR, scraped_at TIMESTAMP",
    "raw_my_teams": "owner VARCHAR, player VARCHAR, role VARCHAR, captain VARCHAR",
    "raw_shots": "Season INT, Gamecode BIGINT, TEAM VARCHAR, ID_PLAYER VARCHAR, PLAYER VARCHAR, ID_ACTION VARCHAR, "
                 "ACTION VARCHAR, POINTS DOUBLE, COORD_X DOUBLE, COORD_Y DOUBLE, ZONE VARCHAR, FASTBREAK DOUBLE, "
                 "SECOND_CHANCE DOUBLE, POINTS_OFF_TURNOVER DOUBLE, MINUTE DOUBLE",
    "raw_referees": "season INT, gamecode BIGINT, slot INT, referee VARCHAR",
    "raw_coaches": "season INT, gamecode BIGINT, home_code VARCHAR, away_code VARCHAR, home_coach VARCHAR, "
                   "away_coach VARCHAR",
    "raw_odds": "event_id VARCHAR, commence_time VARCHAR, home_team VARCHAR, away_team VARCHAR, home_code VARCHAR, "
                "away_code VARCHAR, home_prob DOUBLE, home_margin DOUBLE, total DOUBLE, n_books BIGINT, "
                "fetched_at TIMESTAMP",
    "raw_game_players": "dunkest_id BIGINT, first_name VARCHAR, last_name VARCHAR, position VARCHAR, club VARCHAR, "
                        "price DOUBLE, popularity DOUBLE, prob_playing DOUBLE, is_injured BOOLEAN, is_on_fire BOOLEAN, "
                        "fpt_avg DOUBLE, jersey VARCHAR, matchday BIGINT, popularity_prev DOUBLE, round INT, "
                        "fetched_at TIMESTAMP",
    "raw_gbl_load": "season INT, game_date VARCHAR, esake_game VARCHAR, round_label VARCHAR, club_code VARCHAR, "
                    "opp VARCHAR, result VARCHAR, player VARCHAR, dorsal INT, minutes DOUBLE, pts INT, pir INT",
    "raw_fantasy_rosters": "fantasy_team_id BIGINT, matchday_id BIGINT, dunkest_id BIGINT, first_name VARCHAR, "
                           "last_name VARCHAR, position VARCHAR, club VARCHAR, price DOUBLE, pts DOUBLE, "
                           "is_captain BOOLEAN, court_position INT, starter BOOLEAN, started_from_bench BOOLEAN, "
                           "season INT, round INT",
}
GLOBS = {
    "raw_boxscores": "boxscores_*.parquet",
    "raw_schedule": "schedule_*.parquet",
    "raw_standings": "standings_*.parquet",
    "raw_players": "players_*.parquet",
    "raw_prices": "prices_history.parquet",
    "raw_injuries": "injuries.parquet",
    "raw_fantasy_league": "fantasy_league.parquet",
    "raw_injuries_history": "injuries_history.parquet",
    "raw_my_teams": "my_teams.parquet",
    "raw_shots": "shots_*.parquet",
    "raw_referees": "referees_*.parquet",
    "raw_coaches": "coaches_*.parquet",
    "raw_odds": "odds.parquet",
    "raw_game_players": "game_players.parquet",
    "raw_gbl_load": "gbl_load_*.parquet",
    "raw_fantasy_rosters": "fantasy_rosters.parquet",
}


def main(db_path=WAREHOUSE, raw=RAW, ref=REFERENCE):
    tmp = Path(str(db_path) + ".tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    for table, pattern in GLOBS.items():
        files = sorted(raw.glob(pattern))
        if files:
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet({[str(f) for f in files]}, union_by_name=true)")
        elif table in EMPTY:
            con.execute(f"CREATE TABLE {table} ({EMPTY[table]})")
        else:
            raise FileNotFoundError(f"required source missing: {pattern}")
    # fantasy league manual CSV fallback
    manual_fl = REFERENCE.parent / "manual" / "fantasy_league.csv"
    if not list(raw.glob(GLOBS["raw_fantasy_league"])) and manual_fl.exists():
        con.execute("DROP TABLE raw_fantasy_league")
        con.execute(f"CREATE TABLE raw_fantasy_league AS SELECT * FROM read_csv_auto('{REFERENCE.parent / 'manual' / 'fantasy_league.csv'}')")
    for col, typ in (("team_id", "BIGINT"), ("owner", "VARCHAR"), ("total_pts", "DOUBLE"),
                     ("league_position", "INT")):      # filled when the league comes from the Dunkest API
        con.execute(f"ALTER TABLE raw_fantasy_league ADD COLUMN IF NOT EXISTS {col} {typ}")
    # older injury files lack some columns -> add them so the SQL can rely on them
    for col in ("position_site", "round_info", "source"):
        con.execute(f"ALTER TABLE raw_injuries ADD COLUMN IF NOT EXISTS {col} VARCHAR")
        con.execute(f"ALTER TABLE raw_injuries_history ADD COLUMN IF NOT EXISTS {col} VARCHAR")
    for col in ("birth_country", "headshot", "action_photo", "last_team", "since"):   # player card extras
        con.execute(f"ALTER TABLE raw_players ADD COLUMN IF NOT EXISTS {col} VARCHAR")
    con.execute("ALTER TABLE raw_players ADD COLUMN IF NOT EXISTS weight_kg DOUBLE")
    for name in ("teams", "positions"):
        con.execute(f"CREATE TABLE ref_{name} AS SELECT * FROM read_csv_auto('{ref / (name + '.csv')}', all_varchar=true)")
    con.execute(f"CREATE TABLE settings AS SELECT {WIN_BONUS}::DOUBLE AS win_bonus")

    for f in sorted(SQL_DIR.glob("*.sql")):
        con.execute(f.read_text())
    con.execute("DROP TABLE IF EXISTS raw_boxscores")   # keep the file small; stg has everything
    con.close()
    tmp.replace(db_path)                                # atomic swap: the app never sees a half-built db
    print(f"warehouse built -> {db_path}")


if __name__ == "__main__":
    main()
