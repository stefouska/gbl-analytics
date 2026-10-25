"""Game preview: everything about an upcoming game on one page — prediction (model + betting market), form,
team comparison, head-to-head, key players, injuries, referees and shot profiles."""
import numpy as np
import pandas as pd
import streamlit as st

from lib import common, db, fantasy, labels, ui

st.title("Game preview")
season, as_of = common.season_and_round("gp")
fx = fantasy.fixtures(season, as_of)
res = db.q("SELECT DISTINCT game_key FROM fct_team_game WHERE season = ?", [season])
up = fx[~fx.game_key.isin(res.game_key)].sort_values(["game_date", "start_time"])
if up.empty:
    st.info("No upcoming games in the schedule.")
    st.stop()
up = up[up["round"] <= up["round"].min() + 1]
lab = {r.game_key: f"R{r.round} · {pd.Timestamp(r.game_date):%a %d/%m} {r.start_time or ''} · {r.home_name} – {r.away_name}"
       for r in up.itertuples()}
gk = st.selectbox("Game", list(lab), format_func=lab.get, key="gp_game")
g = up[up.game_key == gk].iloc[0]
H, A = g.home_code, g.away_code
tags = db.q("SELECT club_code, tag, team_name FROM dim_team").set_index("club_code")
hn, an = tags.team_name.get(H, H), tags.team_name.get(A, A)
st.subheader(f"{hn}  vs  {an}")
st.caption(f"Round {g['round']} · {pd.Timestamp(g.game_date):%A %d %B} · {g.start_time or ''} (CET)")

# ---------------- prediction
r = fantasy.team_ratings(season, as_of)
mdl = fantasy.predict_game(r, H, A, use_market=False)
mkt = fantasy.market_lines().get((H, A))
c = st.columns(4)
c[0].metric(f"{g.home} win (model)", f"{mdl['home_win'] * 100:.0f}%" if mdl["home_win"] == mdl["home_win"] else "—")
c[1].metric("Model score", f"{mdl['home_pts']:.0f} – {mdl['away_pts']:.0f}" if mdl["home_pts"] == mdl["home_pts"] else "—",
            f"margin {mdl['margin']:+.1f}" if mdl["margin"] == mdl["margin"] else None, delta_color="off")
if mkt:
    c[2].metric(f"{g.home} win (market)", f"{mkt['home_prob'] * 100:.0f}%" if mkt["home_prob"] == mkt["home_prob"] else "—",
                f"{(mkt['home_prob'] - mdl['home_win']) * 100:+.0f} vs model" if mkt["home_prob"] == mkt["home_prob"] else None,
                delta_color="off")
    c[3].metric("Market score", f"{mkt['home_pts']:.0f} – {mkt['away_pts']:.0f}" if mkt["home_pts"] == mkt["home_pts"] else "—",
                f"total {mkt['total']:.1f}" if mkt["total"] == mkt["total"] else None, delta_color="off")
else:
    c[2].metric("Market", "no line yet")

tg = db.q("SELECT * FROM fct_team_game WHERE club_code IN (?, ?) ORDER BY season, round", [H, A])
cur = tg[tg.season == season]

# ---------------- form
st.markdown("#### Form (last 5)")
fc = st.columns(2)
for col, code, name in ((fc[0], H, hn), (fc[1], A, an)):
    last = cur[cur.club_code == code].tail(5)
    if last.empty:
        col.caption(f"{name}: no games yet")
        continue
    txt = " ".join(("🟢" if w == "W" else "🔴") for w in last.wl)
    det = " · ".join(f"{'vs' if v == 'H' else '@'} {tags.tag.get(o, o)} {int(p)}-{int(op)}"
                     for v, o, p, op in zip(last.venue, last.opp_code, last.pts, last.opp_pts))
    col.markdown(f"**{tags.tag.get(code, code)}** {txt}  \n<small>{det}</small>", unsafe_allow_html=True)

# ---------------- team comparison


def rates(d: pd.DataFrame) -> dict:
    if d.empty:
        return {}
    s = d.sum(numeric_only=True)
    return {"Record": f"{int((d.wl == 'W').sum())}-{int((d.wl == 'L').sum())}",
            "Points": s.pts / len(d), "Points allowed": s.opp_pts / len(d),
            "ORtg": s.pts / s.poss * 100, "DRtg": s.opp_pts / s.opp_poss * 100,
            "Net rating": s.pts / s.poss * 100 - s.opp_pts / s.opp_poss * 100, "Pace": s.poss / len(d),
            "eFG%": (s.fgm + 0.5 * s.fg3m) / s.fga * 100, "Opp eFG%": (s.opp_fgm + 0.5 * s.opp_fg3m) / s.opp_fga * 100,
            "TO%": s.tov / s.poss * 100, "OREB%": s.oreb / (s.oreb + s.opp_dreb) * 100,
            "FT rate": s.fta / s.fga, "3PA rate": s.fg3a / s.fga * 100, "Assists": s.ast / len(d)}


st.markdown("#### Team comparison (this season)")
rh, ra = rates(cur[cur.club_code == H]), rates(cur[cur.club_code == A])
if rh and ra:
    better_low = {"Points allowed", "DRtg", "Opp eFG%", "TO%"}
    rows = []
    for k in rh:
        vh, va = rh[k], ra.get(k)
        if isinstance(vh, str):
            rows.append({"": k, g.home: vh, g.away: va, "Edge": ""})
            continue
        edge = (g.home if (vh < va if k in better_low else vh > va) else g.away) if abs(vh - va) > 1e-9 else "="
        rows.append({"": k, g.home: f"{vh:.1f}" if k != "FT rate" else f"{vh:.2f}",
                     g.away: f"{va:.1f}" if k != "FT rate" else f"{va:.2f}", "Edge": edge})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=36 * (len(rows) + 1) + 2)
else:
    st.caption("Not enough games this season yet.")

# ---------------- head to head
h2h = tg[(tg.club_code == H) & (tg.opp_code == A)]
st.markdown("#### Head to head")
if h2h.empty:
    st.caption("No meetings in the data.")
else:
    hh = h2h.assign(game=lambda d: np.where(d.venue == "H", g.home + " – " + g.away, g.away + " – " + g.home),
                    score=lambda d: np.where(d.venue == "H", d.pts.astype(int).astype(str) + "-" + d.opp_pts.astype(int).astype(str),
                                             d.opp_pts.astype(int).astype(str) + "-" + d.pts.astype(int).astype(str)),
                    winner=lambda d: np.where(d.wl == "W", g.home, g.away),
                    season_lbl=lambda d: d.season.astype(str) + "-" + (d.season + 1).astype(str).str[-2:])
    st.dataframe(hh[["season_lbl", "round", "game", "score", "winner"]].iloc[::-1], hide_index=True, width="stretch",
                 column_config={"season_lbl": "Season", "round": "Round", "game": "Game", "score": "Score",
                                "winner": "Winner"})
    st.caption(f"{g.home} {int((h2h.wl == 'W').sum())} – {int((h2h.wl == 'L').sum())} {g.away}")

# ---------------- key players & injuries
st.markdown("#### Key players (expected fantasy points in this game)")
b = common.board_for_round(season, as_of, int(g["round"]))
kc = st.columns(2)
for col, code in ((kc[0], H), (kc[1], A)):
    with col:
        p = b[b.club_code == code].sort_values("exp_round", ascending=False).head(7)
        labels.show(p, ["player_name", "position", "price", "exp_round", "fp_l3", "inj_flag", "bad_label"],
                    extra={"exp_round": ("EXP", "Expected FP in this game", "d1")}, height=36 * 8 + 2, drop_empty=True)
try:
    inj = db.q("SELECT player, club_code, status, comments FROM fct_injuries WHERE club_code IN (?, ?)", [H, A])
except Exception:
    inj = pd.DataFrame()
if len(inj):
    st.markdown("**Injury report**")
    inj["team"] = inj.club_code.map(tags.tag)
    st.dataframe(inj[["team", "player", "status", "comments"]], hide_index=True, width="stretch")

# ---------------- referees
try:
    rf = db.q("SELECT referee FROM fct_referees WHERE game_key = ? ORDER BY slot", [gk])
except Exception:
    rf = pd.DataFrame()
if len(rf):
    st.markdown("#### Referees")
    st.write(" · ".join(rf.referee))
    st.caption("See the Referees page for their history.")

# ---------------- shot profiles
try:
    sh = db.q("SELECT club_code, opp_code, zone, made FROM fct_shots WHERE season = ? AND (club_code IN (?, ?) "
              "OR opp_code IN (?, ?))", [season, H, A, H, A])
except Exception:
    sh = pd.DataFrame()
if len(sh):
    st.markdown("#### Shot profile: attack vs the other team's defense")
    ZONES = ["At the rim", "Paint", "Mid-range", "Corner 3", "Above the break 3"]

    def prof(d):
        z = d.groupby("zone").made.agg(["size", "mean"]).reindex(ZONES)
        return (z["size"] / z["size"].sum() * 100).round(0), (z["mean"] * 100).round(1)
    rows = []
    for att, dfn, an_, dn_ in ((H, A, g.home, g.away), (A, H, g.away, g.home)):
        s_att, f_att = prof(sh[sh.club_code == att])
        s_def, f_def = prof(sh[sh.opp_code == dfn])
        for z in ZONES:
            rows.append({"Attack": an_, "Zone": z, "Shot share": s_att.get(z), "FG%": f_att.get(z),
                         f"Allowed by {dn_}: share": s_def.get(z), "Allowed FG%": f_def.get(z)})
    t = pd.DataFrame(rows)
    st.dataframe(t, hide_index=True, width="stretch", height=36 * (len(t) + 1) + 2)
st.caption("Model = team ratings (offense, defense, pace, home court). Market = median of the bookmakers when a line "
           "exists (Betting lines page).")
