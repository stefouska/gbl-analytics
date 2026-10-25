"""Read-only access to the warehouse + the generic 'measure engine'."""
from dataclasses import dataclass, field
from pathlib import Path
import os

import duckdb
import pandas as pd

from .metrics import PLAYER_COMPONENTS, PLAYER_METRICS, TEAM_COMPONENTS, TEAM_METRICS

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "warehouse.duckdb"


def db_path() -> Path:
    """Read at every call: the GBL app points WAREHOUSE to its own file."""
    return Path(os.getenv("WAREHOUSE", DEFAULT_DB))


DB_PATH = db_path()                                    # kept for older code


def con():
    return duckdb.connect(str(db_path()), read_only=True)


def q(sql: str, params=None) -> pd.DataFrame:
    with con() as c:
        return c.execute(sql, params or []).df()


@dataclass
class Filters:
    season: int
    phases: list = field(default_factory=list)       # ['RS','PI','PO','FF']
    rounds: tuple = (1, 99)
    venue: str | None = None                           # 'H' | 'A'
    clubs: list = field(default_factory=list)
    countries: list = field(default_factory=list)
    wl: str | None = None                              # 'W' | 'L'
    opp: list = field(default_factory=list)

    def where(self, alias: str) -> tuple[str, list]:
        w, p = [f"{alias}.season = ?", f"{alias}.round BETWEEN ? AND ?"], [self.season, *self.rounds]
        if self.phases:
            w.append(f"{alias}.phase IN ({','.join('?' * len(self.phases))})"); p += self.phases
        if self.venue:
            w.append(f"{alias}.venue = ?"); p.append(self.venue)
        if self.wl:
            w.append(f"{alias}.wl = ?"); p.append(self.wl)
        if self.clubs:
            w.append(f"{alias}.club_code IN ({','.join('?' * len(self.clubs))})"); p += self.clubs
        if self.countries:
            w.append(f"{alias}.club_code IN (SELECT club_code FROM dim_team WHERE country IN "
                     f"({','.join('?' * len(self.countries))}))"); p += self.countries
        if self.opp:
            w.append(f"{alias}.opp_code IN ({','.join('?' * len(self.opp))})"); p += self.opp
        return " AND ".join(w), p


def _select(components, metrics, keys=None):
    comp = ",\n  ".join(f"{sql} AS {name}" for name, sql in components.items())
    ms = [m for m in metrics if keys is None or m.key in keys]
    met = ",\n  ".join(f"{m.sql} AS {m.key}" for m in ms)
    return comp, met


def player_stats(f: Filters, keys=None) -> pd.DataFrame:
    """One row per player, any metric of the catalog, under any filter."""
    comp, met = _select(PLAYER_COMPONENTS, PLAYER_METRICS, keys)
    where, params = f.where("p")
    sql = f"""
    WITH c AS (
      SELECT p.player_id, any_value(p.club_code ORDER BY p.round DESC) AS club_code,
      {comp}
      FROM fct_player_game p JOIN fct_team_game t USING (game_key, club_code)
      WHERE {where}
      GROUP BY p.player_id
    )
    SELECT d.player_name, d.position, dt.tag AS team, d.nationality, d.height_cm, d.age,
           c.player_id, c.club_code, {met}
    FROM c LEFT JOIN dim_player d USING (player_id) LEFT JOIN dim_team dt ON dt.club_code = c.club_code
    WHERE c.gp > 0
    """
    return q(sql, params)


def team_stats(f: Filters, keys=None) -> pd.DataFrame:
    comp, met = _select(TEAM_COMPONENTS, TEAM_METRICS, keys)
    where, params = f.where("t")
    sql = f"""
    WITH c AS (SELECT t.club_code, {comp} FROM fct_team_game t WHERE {where} GROUP BY t.club_code)
    SELECT dt.team_name, dt.tag, dt.country, c.club_code, c.*, {met}
    FROM c LEFT JOIN dim_team dt USING (club_code)
    """
    df = q(sql, params)
    return df.loc[:, ~df.columns.duplicated()]


def player_games(season: int, max_round: int = 99, player_id: str | None = None) -> pd.DataFrame:
    """Game-level rows with the team/opponent context (used for rolling fantasy metrics)."""
    sql = """
    SELECT p.*, d.player_name, d.position, dt.tag AS team,
           t.minutes AS tm_minutes, t.fga AS tm_fga, t.fta AS tm_fta, t.tov AS tm_tov,
           t.pts AS team_pts, t.opp_pts
    FROM fct_player_game p
    JOIN fct_team_game t USING (game_key, club_code)
    LEFT JOIN dim_player d USING (player_id)
    LEFT JOIN dim_team dt ON dt.club_code = p.club_code
    WHERE p.season = ? AND p.round <= ? AND (? IS NULL OR p.player_id = ?)
    ORDER BY p.player_id, p.round
    """
    return q(sql, [season, max_round, player_id, player_id])


def seasons() -> list[int]:
    return q("SELECT DISTINCT season FROM fct_player_game ORDER BY 1 DESC").season.tolist()


def rounds(season: int) -> list[int]:
    return q("SELECT DISTINCT round FROM fct_player_game WHERE season = ? ORDER BY 1", [season])["round"].tolist()


def status() -> dict:
    p = DB_PATH.parent / "raw" / "_status.json"
    import json
    return json.loads(p.read_text()) if p.exists() else {}
