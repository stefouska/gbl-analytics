"""Greek Basketball League (Stoiximan GBL) from the official site esake.gr (no API: the HTML pages are read).

Writes, in data/gbl/raw/, files with the SAME columns as the EuroLeague raw files, so the same SQL model and the
same app pages work for the GBL:
  boxscores_{season}.parquet  one row per player per game + 'Team' and 'Total' rows (like the EuroLeague API)
  schedule_{season}.parquet   every game of the season (played or not)
  players_{season}.parquet    rosters (position, nationality, height, birth date when the site shows them)
  game_ids_{season}.parquet   esake game id (hex) ↔ the integer game code we use (stable between runs)

Pages used (all public):
  EsakeRanking?idchampionship=…                          -> the teams of a season (+ the championship ids)
  EsaketeamView?idteam=…&mode=3&idchampionship=…&idseason=00000001|00000002   -> the team's games (program)
  EsaketeamView?idteam=…&mode=1&idchampionship=…         -> roster
  EsakegameView?idgame=…&mode=3                           -> box score
When a page cannot be parsed, its HTML is saved in data/gbl/raw/_debug/ so the parser can be fixed."""
import re
import time
from datetime import date

import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import RAW as _RAW

BASE = "https://www.esake.gr/el/action"
RAW = _RAW
DEBUG = RAW / "_debug"
UA = {"User-Agent": "Mozilla/5.0 (euroleague-analytics dashboard; daily refresh)", "Accept-Language": "el,en;q=0.8"}
KNOWN_CHAMPIONSHIPS = {2025: "44B80BEB"}          # fallback when the season list cannot be read
PHASES = {"00000001": "RS", "00000002": "PO"}
HEX8 = re.compile(r"^[0-9A-F]{8}$")
GR_MONTHS = {"ιαν": 1, "φεβ": 2, "μαρ": 3, "απρ": 4, "μαϊ": 5, "μαι": 5, "ιουν": 6, "ιουλ": 7, "αυγ": 8, "σεπ": 9,
             "οκτ": 10, "νοε": 11, "δεκ": 12,
             "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
             "nov": 11, "dec": 12}

# short tags and readable names (the site writes the names in Greek capitals)
TEAM_TAGS = [("ΟΛΥΜΠΙΑΚΟΣ", "OLY", "Olympiacos"), ("ΠΑΝΑΘΗΝΑ", "PAO", "Panathinaikos AKTOR"),
             ("ΑΕΚ", "AEK", "AEK"), ("ΠΑΟΚ", "PAOK", "PAOK"), ("ΑΡΗΣ", "ARIS", "Aris"),
             ("ΠΕΡΙΣΤΕΡΙ", "PER", "Peristeri"), ("ΠΡΟΜΗΘΕΑΣ", "PROM", "Promitheas Patras"),
             ("ΚΟΛΟΣΣΟΣ", "KOL", "Kolossos Rodou"), ("ΜΑΡΟΥΣΙ", "MAR", "Maroussi"),
             ("ΚΑΡΔΙΤΣΑ", "KAR", "Karditsa"), ("ΗΡΑΚΛΗΣ", "HER", "Iraklis"), ("ΜΥΚΟΝΟΣ", "MYK", "Mykonos"),
             ("ΠΑΝΙΩΝΙΟΣ", "PANI", "Panionios"), ("ΛΕΥΚΑΔΑΣ", "LEF", "Doxa Lefkadas"),
             ("FALCONS", "FAL", "Vikos Falcons"), ("ΛΑΥΡΙΟ", "LAV", "Lavrio"), ("ΙΩΝΙΚΟΣ", "ION", "Ionikos"),
             ("ΑΠΟΛΛΩΝ", "APO", "Apollon Patras"), ("ΨΥΧΙΚΟ", "PSY", "Psychiko")]


def team_tag(name: str, code: str) -> tuple[str, str]:
    up = (name or "").upper()
    for key, tag, nice in TEAM_TAGS:
        if key in up:
            return tag, nice
    return code[-4:], (name or code).title()


def _get(session, url, params=None, tries=3) -> str:
    for i in range(tries):
        try:
            r = session.get(url, params=params, headers=UA, timeout=30)
            if r.ok:
                time.sleep(0.4)                       # be gentle with their server
                r.encoding = "utf-8"                  # no charset in the header; the pages are UTF-8
                return r.text
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"esake.gr did not answer: {url} {params or ''}")


def _debug(name: str, html: str):
    DEBUG.mkdir(parents=True, exist_ok=True)
    (DEBUG / f"{name}.html").write_text(html, encoding="utf-8")


def _param(href: str, key: str):
    m = re.search(rf"[?&]{key}=([0-9A-Fa-f]+)", href or "")
    return m.group(1).upper() if m else None


# ------------------------------------------------------------------ seasons and teams
def championship_id(session, season: int) -> str:
    """The esake id of season `season` (2025 = 2025-26), read from the season list of the ranking page."""
    label = f"{season}-{season + 1}"
    html = _get(session, f"{BASE}/EsakeRanking")
    soup = BeautifulSoup(html, "html.parser")
    for opt in soup.find_all("option"):
        if label in opt.get_text(" ", strip=True):
            v = (opt.get("value") or "").strip()
            cid = v.upper() if HEX8.match(v.upper()) else _param(v, "idchampionship")
            if cid:
                return cid
    for a in soup.find_all("a", href=True):                 # links instead of a <select>
        if label in a.get_text(" ", strip=True) and _param(a["href"], "idchampionship"):
            return _param(a["href"], "idchampionship")
    if season in KNOWN_CHAMPIONSHIPS:
        return KNOWN_CHAMPIONSHIPS[season]
    _debug(f"ranking_no_{season}", html)
    raise RuntimeError(f"GBL: could not find the championship id of {label}")


def _last_day(soup) -> str | None:
    """The latest round in the ranking page's round list (values like '26-1')."""
    sel = soup.find("select", id="day") or soup.find("select", attrs={"name": "day"})
    vals = [o.get("value", "").strip() for o in (sel.find_all("option") if sel else [])]
    vals = [v for v in vals if re.fullmatch(r"\d{1,2}-\d+", v)]
    return vals[-1] if vals else None


def teams(session, cid: str) -> dict:
    """{team id: name} from the ranking table of the season. esake.gr shows the table only when a round is chosen,
    so the page is read once for its list of rounds and again with the latest one."""
    html = _get(session, f"{BASE}/EsakeRanking", {"idchampionship": cid})
    soup = BeautifulSoup(html, "html.parser")
    day = _last_day(soup)
    if day:
        html = _get(session, f"{BASE}/EsakeRanking", {"idchampionship": cid, "day": day})
        soup = BeautifulSoup(html, "html.parser")
    out = {}
    for table in soup.find_all("table"):
        for a in table.find_all("a", href=True):
            tid = _param(a["href"], "idteam")
            if tid:
                out[tid] = re.sub(r"^\d+\s*", "", a.get_text(" ", strip=True))
    if not out:                                   # last resort: the club bar at the top (current season's clubs)
        for a in soup.select(".teams-bar a[href], .teams-bar-mobile a[href]"):
            tid = _param(a["href"], "idteam")
            if tid:
                out[tid] = a.get("title") or a.get_text(" ", strip=True)
    if not out:
        _debug(f"ranking_{cid}", html)
    return out


# ------------------------------------------------------------------ program (games of a team)
def _parse_day(txt: str, season: int):
    """'Δευ 8 Ιουν 21:00' / 'Sat 28 Sep - 19:00' -> (date, 'HH:MM')."""
    t = (txt or "").lower().replace(".", " ")
    m = re.search(r"(\d{1,2})\s+([a-zα-ωάέήίόύώϊϋ]+)", t)
    if not m:
        return None, None
    mon = next((v for k, v in GR_MONTHS.items() if m.group(2).replace("ά", "α").replace("ί", "ι").startswith(k)), None)
    if not mon:
        return None, None
    year = season if mon >= 8 else season + 1
    tm = re.search(r"(\d{1,2}):(\d{2})", t)
    return date(year, mon, int(m.group(1))), (f"{int(tm.group(1)):02d}:{tm.group(2)}" if tm else None)


def program(session, cid: str, tid: str, season: int, idseason: str) -> list[dict]:
    """Games of team `tid` in a phase: round label, date, time, esake game id."""
    html = _get(session, f"{BASE}/EsaketeamView",
                {"idteam": tid, "mode": 3, "idchampionship": cid, "idseason": idseason, "series": ""})
    soup = BeautifulSoup(html, "html.parser")
    games, cur_round, cur_day = {}, None, None
    for el in soup.find_all(["h5", "img", "a"]):
        if el.name == "h5":
            cur_round, cur_day = el.get_text(" ", strip=True), None
        elif el.name == "img" and "clock" in (el.get("src") or ""):
            nxt = el.next_sibling
            while nxt is not None and not str(getattr(nxt, "string", nxt) or "").strip():
                nxt = nxt.next_sibling
            cur_day = str(getattr(nxt, "string", nxt) or "").strip() if nxt is not None else None
        elif el.name == "a" and "EsakegameView" in (el.get("href") or ""):
            gid = _param(el["href"], "idgame")
            if gid and gid not in games and cur_round:
                d, t = _parse_day(cur_day, season)
                games[gid] = {"esake_id": gid, "round_label": cur_round, "date": d, "time": t,
                              "phase": PHASES.get(idseason, "RS")}
    if not games:
        _debug(f"program_{tid}_{idseason}", html)
    return list(games.values())


def _round_number(label: str):
    m = re.match(r"\s*(\d{1,2})(?!\d)", label or "")
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ box score
NUM = re.compile(r"-?\d+")


def _ma(txt):
    """'7 - 10' -> (7, 10)"""
    n = NUM.findall(txt or "")
    return (int(n[0]), int(n[1])) if len(n) >= 2 else (0, 0)


def _int(txt):
    n = NUM.findall(txt or "")
    return int(n[0]) if n else 0


def _minutes(txt):
    """'00:33:51' or '33:51' -> '33:51'"""
    p = re.findall(r"\d+", txt or "")
    if len(p) == 3:
        return f"{int(p[0]) * 60 + int(p[1])}:{p[2]}"
    if len(p) == 2:
        return f"{int(p[0])}:{p[1]}"
    return "0:00"


def parse_boxscore(html: str) -> list[dict]:
    """Rows of both teams in the EuroLeague raw format. Header cells are matched by name, so the column order
    on the site does not matter. Player rows carry 'idplayer=…&idteam=…' links."""
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []
    for table in soup.find_all("table"):
        if "2PM-A" not in table.get_text(" ", strip=True):
            continue
        header, team_id = None, None
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            texts = [c.get_text(" ", strip=True) for c in cells]
            if header is None:
                if "2PM-A" in texts:
                    header = [t.upper() for t in texts]
                continue
            if not texts or len(texts) < len(header) - 2:
                continue
            col = {h: (texts[i] if i < len(texts) else "") for i, h in enumerate(header)}
            a = cells[0].find("a", href=True)
            first = texts[0].upper()
            if a and _param(a["href"], "idplayer"):
                team_id = team_id or _param(a["href"], "idteam")
                pid = _param(a["href"], "idplayer")
                name = re.sub(r"^#\s*\d+\s*", "", texts[0]).strip()
                kind = "player"
            elif "ΟΜΑΔ" in first or first.startswith("TEAM"):
                pid, name, kind = "Team", "Team", "team"
            elif "ΣΥΝΟΛ" in first or first.startswith("TOTAL"):
                pid, name, kind = "Total", "Total", "total"
            else:
                continue
            f2, f3, ft = _ma(col.get("2PM-A")), _ma(col.get("3PM-A")), _ma(col.get("FTM-A"))
            rows_out.append({
                "_team": team_id, "_kind": kind, "Player_ID": pid, "Player": name,
                "Minutes": _minutes(col.get("TIM.PL.", "")) if kind == "player" else None,
                "Points": _int(col.get("P")), "FieldGoalsMade2": f2[0], "FieldGoalsAttempted2": f2[1],
                "FieldGoalsMade3": f3[0], "FieldGoalsAttempted3": f3[1], "FreeThrowsMade": ft[0],
                "FreeThrowsAttempted": ft[1], "TotalRebounds": _int(col.get("REBS")),
                "DefensiveRebounds": _int(col.get("D.REBS")), "OffensiveRebounds": _int(col.get("O.REBS")),
                "Assistances": _int(col.get("AST")), "BlocksFavour": _int(col.get("BLK")),
                "BlocksAgainst": _int(col.get("BLK-A")), "FoulsReceived": _int(col.get("FOULS F")),
                "FoulsCommited": _int(col.get("FOULS M")), "Steals": _int(col.get("STL")),
                "Turnovers": _int(col.get("TO")), "Valuation": _int(col.get("RANK"))})
        for r in rows_out:                                  # team/total rows belong to this table's team
            if r["_team"] is None:
                r["_team"] = team_id
    return rows_out


# ------------------------------------------------------------------ rosters
POS_WORDS = {"G": ("GUARD", "ΓΚΑΡΝΤ", "ΠΟΪΝΤ", "ΠΟΙΝΤ", "ΣΟΥΤΕΡ", "PG", "SG"),
             "F": ("FORWARD", "ΦΟΡΓΟΥΑΡΝΤ", "ΦΟΡΟΥΑΡΝΤ", "SF", "PF"), "C": ("CENTER", "CENTRE", "ΣΕΝΤΕΡ")}


def roster(session, cid: str, tid: str, season: int) -> list[dict]:
    html = _get(session, f"{BASE}/EsaketeamView", {"idteam": tid, "mode": 1, "idchampionship": cid})
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for a in soup.find_all("a", href=True):
        pid = _param(a["href"], "idplayer")
        if not pid or pid in out:
            continue
        box = a.find_parent(["tr", "li", "div"]) or a
        txt = box.get_text(" ", strip=True).upper()
        pos = next((p for p, words in POS_WORDS.items() if any(w in txt for w in words)), None)
        h = re.search(r"\b(1[6-9]\d|2[0-3]\d)\s*(CM|ΕΚ)?\b", txt)
        y = re.search(r"\b(19[6-9]\d|20[01]\d)\b", txt)
        out[pid] = {"season": season, "player_id": pid, "player_name": a.get_text(" ", strip=True), "club_code": tid,
                    "position_api": {"G": "Guard", "F": "Forward", "C": "Center"}.get(pos), "dorsal": None,
                    "nationality": None, "height_cm": float(h.group(1)) if h else None,
                    "birth_date": f"{y.group(1)}-07-01" if y else None, "active": True}
    return list(out.values())


# ------------------------------------------------------------------ main
def _ids(season: int, esake_ids: list[str]) -> dict:
    """esake hex id -> integer game code, stable between runs (new games get the next numbers)."""
    f = RAW / f"game_ids_{season}.parquet"
    old = pd.read_parquet(f) if f.exists() else pd.DataFrame(columns=["esake_id", "gamecode"])
    known = dict(zip(old.esake_id, old.gamecode.astype(int)))
    nxt = max(known.values(), default=0) + 1
    for g in esake_ids:
        if g not in known:
            known[g], nxt = nxt, nxt + 1
    pd.DataFrame({"esake_id": list(known), "gamecode": list(known.values())}).to_parquet(f, index=False)
    return known


def fetch(season: int, full: bool = False) -> pd.DataFrame:
    RAW.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    cid = championship_id(s, season)
    tms = teams(s, cid)
    if not tms:
        raise RuntimeError("GBL: no teams found in the ranking table (HTML saved in data/gbl/raw/_debug)")
    # 1. every game of the season from the teams' programs
    games = {}
    for tid in tms:
        for idseason in PHASES:
            try:
                for g in program(s, cid, tid, season, idseason):
                    games.setdefault(g["esake_id"], g)
            except RuntimeError:
                pass
    if not games:
        raise RuntimeError("GBL: no games found in the team programs (HTML saved in data/gbl/raw/_debug)")
    sched = pd.DataFrame(games.values())
    sched["round"] = sched.round_label.map(_round_number)
    rs_max = sched.loc[sched.phase == "RS", "round"].max()
    rs_max = int(rs_max) if rs_max == rs_max and rs_max is not None else 0
    po = sched.phase == "PO"                                 # playoffs continue the round numbering by date
    if po.any():
        sched.loc[po, "round"] = rs_max + sched.loc[po, "date"].rank(method="dense").astype(int)
    sched["round"] = sched["round"].fillna(0).astype(int)
    ids = _ids(season, list(sched.esake_id))
    sched["gamecode"] = sched.esake_id.map(ids)

    # 2. box scores: games not stored yet (that have started) + the last 10 days (corrections)
    bf = RAW / f"boxscores_{season}.parquet"
    old = pd.read_parquet(bf) if bf.exists() and not full else pd.DataFrame()
    have = set(old.Gamecode.unique()) if len(old) else set()
    today = date.today()
    due = sched[sched.date.notna() & (sched.date <= today)]
    todo = due[~due.gamecode.isin(have) | (pd.to_datetime(due.date) >= pd.Timestamp(today) - pd.Timedelta(days=10))]
    frames, failed, played = [], 0, {}
    for g in todo.itertuples():
        try:
            html = _get(s, f"{BASE}/EsakegameView", {"idgame": g.esake_id, "mode": 3})
            rows = parse_boxscore(html)
        except RuntimeError:
            failed += 1
            continue
        if not rows:
            if not failed:
                _debug(f"game_{g.esake_id}", html)
            continue
        df = pd.DataFrame(rows)
        tot = df[df._kind == "total"].set_index("_team").Points
        if len(tot) != 2 or tot.sum() == 0:
            continue                                        # not played yet
        team_order = list(dict.fromkeys(df._team))          # first table = home team
        df["Team"] = df._team
        df["Home"] = (df._team == team_order[0]).astype(int)
        df["IsStarter"] = 0
        df["Plusminus"] = None
        df["Season"], df["Phase"], df["Round"], df["Gamecode"] = season, g.phase, int(g.round), int(g.gamecode)
        frames.append(df.drop(columns=["_team", "_kind"]))
        played[g.esake_id] = (team_order[0], team_order[1], int(tot[team_order[0]]), int(tot[team_order[1]]))
    new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(new):
        out = pd.concat([old[~old.Gamecode.isin(new.Gamecode.unique())], new], ignore_index=True) if len(old) else new
        out.to_parquet(bf, index=False)
    else:
        out = old

    # 3. schedule in the EuroLeague format (home/away from the box score; unknown for games not played)
    sf = RAW / f"schedule_{season}.parquet"
    prev = pd.read_parquet(sf) if sf.exists() else pd.DataFrame()
    prev_teams = {r.gamecode: (r.homecode, r.awaycode) for r in prev.itertuples()} if len(prev) else {}
    rows = []
    for g in sched.itertuples():
        h, a = (played[g.esake_id][:2] if g.esake_id in played else
                prev_teams.get(f"E{season}_{g.gamecode}", (None, None)))
        if h is None and len(out):
            gb = out[out.Gamecode == g.gamecode]
            if len(gb):
                h = gb[gb.Home == 1].Team.iloc[0]
                a = gb[gb.Home == 0].Team.iloc[0]
        if h is None:
            continue                                        # opponent not known yet (filled once played)
        (ht, hn), (at, an) = team_tag(tms.get(h, h), h), team_tag(tms.get(a, a), a)
        rows.append({"season": season, "gameday": int(g.round), "round": g.round_label,
                     "gamecode": f"E{season}_{g.gamecode}", "date": str(g.date) if g.date else None,
                     "startime": g.time, "homecode": h, "hometv": ht, "hometeam": hn,
                     "awaycode": a, "awaytv": at, "awayteam": an,
                     "played": "true" if len(out) and (out.Gamecode == g.gamecode).any() else "false"})
    pd.DataFrame(rows).to_parquet(sf, index=False)

    # 4. rosters (positions, height, age when the site shows them)
    pl = []
    for tid in tms:
        try:
            pl += roster(s, cid, tid, season)
        except RuntimeError:
            pass
    if pl:
        pd.DataFrame(pl).to_parquet(RAW / f"players_{season}.parquet", index=False)
    print(f"GBL {season}-{str(season + 1)[-2:]}: {len(tms)} teams, {len(sched)} games, "
          f"{out.Gamecode.nunique() if len(out) else 0} box scores ({len(todo)} checked, {failed} failed), "
          f"{len(pl)} roster players", flush=True)
    return out
