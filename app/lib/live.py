"""Live data from the EuroLeague Game Center (live.euroleague.net), read at page load and cached ~25 seconds.
Box scores come through the euroleague_api package (the same call the daily job uses); the game header
(quarter, clock) is read directly and every field is optional, so a change on their side degrades gracefully."""
import os
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st

COMPETITION = "E"
HEADER_URL = "https://live.euroleague.net/api/Header"
UA = {"User-Agent": "Mozilla/5.0 (euroleague-analytics dashboard)"}


def gamecode(game_key: str) -> int:
    """'E2026_17' -> 17"""
    return int(str(game_key).rsplit("_", 1)[-1])


RAW = Path(__file__).resolve().parents[2] / "data" / "raw"


@st.cache_data(ttl=3600, show_spinner=False)
def _stored(season: int) -> pd.DataFrame:
    f = RAW / f"boxscores_{season}.parquet"
    return pd.read_parquet(f) if f.exists() else pd.DataFrame()


def stored_boxscore(season: int, code: int) -> pd.DataFrame:
    """Box score of a game already in the daily data (no call to the Game Center)."""
    s = _stored(season)
    return s[s.Gamecode == code] if not s.empty else s


@st.cache_data(ttl=25, show_spinner=False)
def boxscore(season: int, code: int) -> pd.DataFrame:
    """Player rows + 'Team'/'Total' rows of both teams; empty before tip-off."""
    if os.getenv("LIVE_FROM_PARQUET"):                  # tests without internet
        return stored_boxscore(season, code)
    try:
        from euroleague_api.boxscore_data import BoxScoreData
        return BoxScoreData(COMPETITION).get_players_boxscore_stats(season, code)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=25, show_spinner=False)
def header(season: int, code: int) -> dict:
    try:
        r = requests.get(HEADER_URL, params={"gamecode": code, "seasoncode": f"{COMPETITION}{season}"},
                         headers=UA, timeout=10)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def _get(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def parse_score(bx: pd.DataFrame) -> dict:
    """Score per team from the 'Total' rows of the box score."""
    if bx.empty or "Player_ID" not in bx:
        return {}
    tot = bx[bx.Player_ID.astype(str).str.strip() == "Total"]
    return {str(r.Team).strip(): int(r.Points) for r in tot.itertuples() if pd.notna(r.Points)}


def parse_players(bx: pd.DataFrame) -> pd.DataFrame:
    if bx.empty or "Player_ID" not in bx:
        return pd.DataFrame()
    p = bx[~bx.Player_ID.astype(str).str.strip().isin(["Team", "Total"])].copy()

    def mins(x):
        try:
            m, s = str(x).split(":")[:2]
            return int(m) + int(s) / 60
        except ValueError:
            return 0.0
    out = pd.DataFrame({
        "player_id": p.Player_ID.astype(str).str.strip(), "player": p.Player, "team": p.Team.astype(str).str.strip(),
        "home": p.Home, "on_court": p.get("IsPlaying", pd.Series(0, index=p.index)).fillna(0).astype(float) > 0,
        "five": p.get("IsStarter", pd.Series(0, index=p.index)).fillna(0).astype(float) > 0,
        "min": p.Minutes.map(mins), "pts": p.Points, "reb": p.TotalRebounds, "ast": p.Assistances,
        "stl": p.Steals, "tov": p.Turnovers, "blk": p.BlocksFavour, "pf": p.FoulsCommited,
        "fg2": p.FieldGoalsMade2.astype("Int64").astype(str) + "/" + p.FieldGoalsAttempted2.astype("Int64").astype(str),
        "fg3": p.FieldGoalsMade3.astype("Int64").astype(str) + "/" + p.FieldGoalsAttempted3.astype("Int64").astype(str),
        "ft": p.FreeThrowsMade.astype("Int64").astype(str) + "/" + p.FreeThrowsAttempted.astype("Int64").astype(str),
        "pir": p.Valuation, "pm": p.get("Plusminus", pd.Series(np.nan, index=p.index)),
        "oreb": p.get("OffensiveRebounds"), "dreb": p.get("DefensiveRebounds"), "blka": p.get("BlocksAgainst"),
        "fd": p.get("FoulsReceived"),
        "fgm2": p.FieldGoalsMade2, "fga2": p.FieldGoalsAttempted2, "fgm3": p.FieldGoalsMade3,
        "fga3": p.FieldGoalsAttempted3, "ftm": p.FreeThrowsMade, "fta": p.FreeThrowsAttempted})
    return out


def parse_status(hd: dict, score: dict, start: pd.Timestamp, now: pd.Timestamp) -> tuple[str, str]:
    """('scheduled'|'live'|'final', label)."""
    live_flag = _get(hd, "Live", "live")
    quarter = _get(hd, "Quarter", "quarter")
    clock = _get(hd, "RemaningPartialTime", "RemainingPartialTime", "remainingPartialTime")
    has_score = bool(score) and sum(score.values()) > 0
    if not has_score:
        return ("scheduled", f"{start:%H:%M}") if pd.notna(start) else ("scheduled", "")
    if live_flag is True or (live_flag is None and pd.notna(start) and now < start + pd.Timedelta(hours=2, minutes=30)):
        q = f"Q{quarter}" if quarter and str(quarter).isdigit() and int(quarter) <= 4 else ("OT" if quarter else "")
        return "live", f"🔴 LIVE {q} {clock or ''}".strip()
    return "final", "Final"


def quarter_scores(hd: dict) -> pd.DataFrame:
    """Quarter-by-quarter points if the header has them (ScoreQuarter1A ... / ScoreQuarter1B ...)."""
    rows = {}
    for side in ("A", "B"):
        qs = {f"Q{i}": _get(hd, f"ScoreQuarter{i}{side}") for i in range(1, 5)}
        ot = _get(hd, f"ScoreExtraTime{side}")
        if ot not in (None, "0", 0):
            qs["OT"] = ot
        if any(v not in (None, "") for v in qs.values()):
            rows[_get(hd, f"Team{side}", f"CodeTeam{side}", default=side)] = qs
    return pd.DataFrame(rows).T if rows else pd.DataFrame()


# ---------------------------------------------------------------- play by play, one line per play
PBP_URL = "https://live.euroleague.net/api/PlaybyPlay"
PERIODS = ["FirstQuarter", "SecondQuarter", "ThirdQuarter", "ForthQuarter", "ExtraTime"]
PLAYS = {   # code -> (emoji, text, key play?)
    "3FGM": ("🎯", "three-pointer", True), "2FGM": ("🏀", "two points", True), "LAYUPMD": ("🏀", "layup", True),
    "DUNK": ("💥", "DUNK", True), "FTM": ("✅", "free throw made", True), "FTA": ("❌", "free throw missed", False),
    "3FGA": ("➖", "missed three", False), "2FGA": ("➖", "missed two", False), "LAYUPATT": ("➖", "missed layup", False),
    "3FGAB": ("🧱", "three blocked", False), "2FGAB": ("🧱", "shot blocked", False),
    "O": ("🔁", "offensive rebound", False), "D": ("🛡️", "defensive rebound", False), "AS": ("🤝", "assist", False),
    "ST": ("🖐️", "steal", True), "TO": ("⚠️", "turnover", False), "FV": ("🚫", "BLOCK", True),
    "AG": ("🧱", "blocked", False), "CM": ("🟨", "foul", False), "RV": ("🎁", "drew the foul", False),
    "CMU": ("🟥", "unsportsmanlike foul", True), "CMT": ("🟥", "technical foul", True),
    "CMD": ("⛔", "disqualifying foul", True), "C": ("🟨", "coach foul", True), "B": ("🟨", "bench foul", True),
    "OF": ("🟧", "offensive foul", False), "IN": ("🔄", "in", False), "OUT": ("🔄", "out", False),
    "TOUT": ("⏸️", "timeout", True), "TOUT_TV": ("📺", "TV timeout", False), "BP": ("🟢", "start of the quarter", True),
    "EP": ("🔔", "end of the quarter", True), "EG": ("🏁", "END OF THE GAME", True), "TPOFF": ("⬆️", "tip-off", True),
    "JB": ("⬆️", "jump ball", False),
}


def play_by_play(season: int, code: int) -> pd.DataFrame:
    """Every play of a game (Game Center), oldest first, with quarter, clock, team, player, score and one line of
    text with an emoji. Empty if the game has not started or the Game Center does not answer."""
    try:
        r = requests.get(PBP_URL, params={"gamecode": code, "seasoncode": f"{COMPETITION}{season}"}, timeout=15)
        js = r.json() if r.ok else {}
    except Exception:
        return pd.DataFrame()
    rows = []
    for q, key in enumerate(PERIODS, 1):
        for p in js.get(key) or []:
            rows.append({"q": q, "clock": str(p.get("MARKERTIME") or "").strip(), "team": str(p.get("CODETEAM") or "").strip(),
                         "player": str(p.get("PLAYER") or "").replace("  ", " ").strip(),
                         "type": str(p.get("PLAYTYPE") or "").strip(), "info": str(p.get("PLAYINFO") or "").strip(),
                         "a": p.get("POINTS_A"), "b": p.get("POINTS_B")})
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    d["a"] = pd.to_numeric(d.a, errors="coerce").ffill().fillna(0).astype(int)
    d["b"] = pd.to_numeric(d.b, errors="coerce").ffill().fillna(0).astype(int)
    d["n"] = range(len(d))
    d["key"] = d.type.map(lambda t: PLAYS.get(t, ("", "", False))[2])
    return d


def play_line(r, home: str, away: str, name=lambda s: s, team_tag=lambda c: c) -> str:
    emo, txt, _ = PLAYS.get(r.type, ("•", r.info or r.type, False))
    qn = f"Q{r.q}" if r.q <= 4 else "OT"
    who = name(r.player) if r.player else ""
    tt = team_tag(r.team) if r.team else ""
    team = f" ({tt})" if tt and who else (f" {tt}" if tt else "")
    if r.type in ("BP", "EP", "EG", "TPOFF"):
        return f"{qn} {r.clock} · {emo} {txt} · {home} {r.a}-{r.b} {away}"
    line = f"{qn} {r.clock} · {emo} {who}{team} {txt}".replace("  ", " ")
    if r.type in ("3FGM", "2FGM", "LAYUPMD", "DUNK", "FTM"):
        line += f" · **{r.a}-{r.b}**"
    return line


# ---------------------------------------------------------------- box score in the EuroLeague Game Center layout
def _short(name: str) -> str:
    """'JONES, CARLIK' -> 'C. Jones'"""
    s = str(name or "")
    if "," in s:
        last, first = [x.strip() for x in s.split(",", 1)]
        return f"{first[:1].upper()}. {last.title()}" if first else last.title()
    return s.title()


def _pct(m, a):
    return f"{(m / a * 100):.1f}%" if a else "0.0%"


def gamecenter_table(bx: pd.DataFrame, team: str) -> pd.DataFrame:
    """One team's box score exactly as the Game Center shows it: # Player (* = starter), Min, PTS, 2FG, 3FG, FT
    (M/A and %), rebounds O/D/T, AST, STL, TO, blocks FV/AG, fouls CM/RV, PIR, +/- — then the Team and Totals rows."""
    if bx.empty or "Player_ID" not in bx:
        return pd.DataFrame()
    t = bx[bx.Team.astype(str).str.strip() == team].copy()
    if t.empty:
        return pd.DataFrame()
    kind = t.Player_ID.astype(str).str.strip()
    num = lambda c: pd.to_numeric(t.get(c), errors="coerce").fillna(0).astype(int)  # noqa: E731
    m2, a2, m3, a3, mf, af = (num(c) for c in ("FieldGoalsMade2", "FieldGoalsAttempted2", "FieldGoalsMade3",
                                               "FieldGoalsAttempted3", "FreeThrowsMade", "FreeThrowsAttempted"))
    starter = pd.to_numeric(t.get("IsStarter"), errors="coerce").fillna(0) > 0
    name = [("Team" if k == "Team" else "Totals" if k == "Total" else
             f"{str(d).strip()} {_short(p)}{'*' if s else ''}".strip())
            for k, d, p, s in zip(kind, t.get("Dorsal", pd.Series("", index=t.index)).fillna(""), t.Player, starter)]
    pm = pd.to_numeric(t.get("Plusminus"), errors="coerce")
    out = pd.DataFrame({
        "Player": name, "Min": t.Minutes.fillna("").astype(str).str.replace(r"^(\d+:\d{2}).*$", r"\1", regex=True)
        .replace({"nan": "", "DNP": "00:00", "None": ""}),
        "PTS": num("Points"),
        "2FG M/A": m2.astype(str) + "/" + a2.astype(str), "2FG %": [_pct(m, a) for m, a in zip(m2, a2)],
        "3FG M/A": m3.astype(str) + "/" + a3.astype(str), "3FG %": [_pct(m, a) for m, a in zip(m3, a3)],
        "FT M/A": mf.astype(str) + "/" + af.astype(str), "FT %": [_pct(m, a) for m, a in zip(mf, af)],
        "O": num("OffensiveRebounds"), "D": num("DefensiveRebounds"), "T": num("TotalRebounds"),
        "AST": num("Assistances"), "STL": num("Steals"), "TO": num("Turnovers"),
        "FV": num("BlocksFavour"), "AG": num("BlocksAgainst"), "CM": num("FoulsCommited"), "RV": num("FoulsReceived"),
        "PIR": num("Valuation"), "+/-": pm.map(lambda v: "" if v != v else f"{int(v):+d}"),
        "_order": [2 if k == "Total" else 1 if k == "Team" else 0 for k in kind],
        "_dorsal": pd.to_numeric(t.get("Dorsal"), errors="coerce").fillna(999)})
    team_row = out._order == 1
    for c in ("2FG M/A", "2FG %", "3FG M/A", "3FG %", "FT M/A", "FT %", "+/-", "Min"):
        out[c] = out[c].astype(object)
        out.loc[team_row, c] = ""
    out.loc[out._order == 2, "+/-"] = ""
    return out.sort_values(["_order", "_dorsal"]).drop(columns=["_order", "_dorsal"]).reset_index(drop=True)


def coaches(hd: dict) -> tuple[str, str]:
    """(home coach, away coach) from the Game Center header, when it has them."""
    a = _get(hd, "CoachA", "coachA", "TeamACoach", default="") or ""
    b = _get(hd, "CoachB", "coachB", "TeamBCoach", default="") or ""
    return str(a).strip(), str(b).strip()
