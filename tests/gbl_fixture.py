"""Fake esake.gr pages (same structure as the real ones) to test the GBL parser and the GBL app offline."""
import random

TEAMS = {"00000002": "ΟΛΥΜΠΙΑΚΟΣ", "00000001": "ΠΑΝΑΘΗΝΑΪΚΟΣ AKTOR", "00000010": "ΑΕΚ", "0000000C": "ΠΑΟΚ"}
HDR = ("<tr><th></th><th></th><th colspan=3>SHOTS</th><th colspan=3>REBOUNDS</th></tr>"
       "<tr><td>ΠΑΙΚΤΗΣ</td><td>P</td><td>2PM-A</td><td>3PM-A</td><td>FTM-A</td><td>REBS</td><td>D.REBS</td>"
       "<td>O.REBS</td><td>AST</td><td>BLK</td><td>BLK-A</td><td>FOULS F</td><td>FOULS M</td><td>STL</td><td>TO</td>"
       "<td>TIM.PL.</td><td>RANK</td></tr>")


def ranking():
    opts = ("<select><option value='AAAA2026'>Stoiximan GBL 2026-2027</option>"
            "<option value='44B80BEB'>Stoiximan GBL 2025-2026</option></select>")
    rows = "".join(f"<tr><td><a href='/el/action/EsaketeamView?idteam={t}&mode=1'>{i + 1} {n}</a></td><td>2</td></tr>"
                   for i, (t, n) in enumerate(TEAMS.items()))
    nav = "<div><a href='/el/action/EsaketeamView?idteam=0041ADCB&mode=1'>VIKOS FALCONS</a></div>"
    return f"<html><body>{nav}{opts}<table>{rows}</table></body></html>"


def schedule_pairs():
    t = list(TEAMS)
    return [(1, t[0], t[1], "A0000001"), (1, t[2], t[3], "A0000002"),
            (2, t[1], t[2], "A0000003"), (2, t[3], t[0], "A0000004")]


def program(tid):
    parts = []
    for rnd, h, a, gid in schedule_pairs():
        if tid in (h, a):
            parts.append(f"<h5>{rnd:02d} series</h5><img src='/skn/clock.svg'>Sat {3 + rnd} Oct - 19:00"
                         f"<a href='/el/action/EsakegameView?idgame={gid}&mode=3'>Stats</a>")
    return "<html><body>" + "".join(parts) + "</body></html>"


def _rows(tid, seed):
    rnd = random.Random(seed)
    out, tot = [], [0] * 15
    for i in range(8):
        f2m, f2a = rnd.randint(0, 6), 0
        f2a = f2m + rnd.randint(0, 4)
        f3m = rnd.randint(0, 3)
        f3a = f3m + rnd.randint(0, 3)
        ftm = rnd.randint(0, 4)
        fta = ftm + rnd.randint(0, 2)
        pts = 2 * f2m + 3 * f3m + ftm
        dr, orb, ast, stl, to = rnd.randint(0, 6), rnd.randint(0, 2), rnd.randint(0, 5), rnd.randint(0, 2), rnd.randint(0, 3)
        pid = f"{tid[-4:]}{i:04d}"
        out.append(f"<tr><td><a href='/el/action/EsakeplayerView?idplayer={pid}&mode=1&idteam={tid}&idchampionship=1'>"
                   f"#{i} PLAYER{i} {tid[-2:]}</a></td><td>{pts}</td><td>{f2m} - {f2a}</td><td>{f3m} - {f3a}</td>"
                   f"<td>{ftm} - {fta}</td><td>{dr + orb}</td><td>{dr}</td><td>{orb}</td><td>{ast}</td><td>0</td><td>0</td>"
                   f"<td>2</td><td>2</td><td>{stl}</td><td>{to}</td><td>00:25:00</td><td>{pts + dr + orb + ast - to}</td></tr>")
        for k, v in enumerate([pts, f2m, f2a, f3m, f3a, ftm, fta, dr + orb, dr, orb, ast, stl, to]):
            tot[k] += v
    out.append("<tr><td>ΟΜΑΔΙΚΑ - ΠΑΓΚΟΣ</td><td>-</td><td>-</td><td>-</td><td>-</td><td>3</td><td>2</td><td>1</td>"
               "<td>-</td><td>-</td><td>-</td><td>-</td><td>0</td><td>-</td><td>1</td><td>-</td><td>-</td></tr>")
    out.append(f"<tr><td>ΣΥΝΟΛΟ</td><td>{tot[0]}</td><td>{tot[1]} - {tot[2]}</td><td>{tot[3]} - {tot[4]}</td>"
               f"<td>{tot[5]} - {tot[6]}</td><td>{tot[7] + 3}</td><td>{tot[8] + 2}</td><td>{tot[9] + 1}</td><td>{tot[10]}</td>"
               f"<td>0</td><td>0</td><td>16</td><td>16</td><td>{tot[11]}</td><td>{tot[12] + 1}</td><td>--:--:--</td><td>80</td></tr>")
    return "".join(out)


def game(gid):
    rnd, h, a, _ = next(p for p in schedule_pairs() if p[3] == gid)
    if rnd == 2 and gid == "A0000004":                       # not played yet
        return "<html><body><h5>02 - Regular season</h5></body></html>"
    return (f"<html><body><h5>{rnd:02d}</h5>ΑΝΑΛΥΤΙΚΑ ΣΤΑΤΙΣΤΙΚΑ <a href='?idteam={h}'>X</a>"
            f"<table>{HDR}{_rows(h, gid + 'h')}</table>ΑΝΑΛΥΤΙΚΑ ΣΤΑΤΙΣΤΙΚΑ <a href='?idteam={a}'>Y</a>"
            f"<table>{HDR}{_rows(a, gid + 'a')}</table></body></html>")


def roster(tid):
    rows = "".join(f"<tr><td><a href='/el/action/EsakeplayerView?idplayer={tid[-4:]}{i:04d}'>PLAYER{i}</a></td>"
                   f"<td>{['ΓΚΑΡΝΤ', 'ΦΟΡΓΟΥΑΡΝΤ', 'ΣΕΝΤΕΡ'][i % 3]}</td><td>{190 + i} cm</td><td>{1995 + i}</td></tr>"
                   for i in range(8))
    return f"<html><body><table>{rows}</table></body></html>"


def fake_get(session, url, params=None, tries=3):
    params = params or {}
    if url.endswith("EsakeRanking"):
        return ranking()
    if url.endswith("EsaketeamView"):
        return program(params["idteam"]) if params.get("mode") == 3 else roster(params["idteam"])
    if url.endswith("EsakegameView"):
        return game(params["idgame"])
    raise RuntimeError(url)
