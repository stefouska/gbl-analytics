import pandas as pd
import streamlit as st

from lib import db, ui

ui.header("Dashboard", "Season overview — team totals, leaders and standings")
f, extra = ui.sidebar_filters("dash", with_player_attrs=True)

teams = db.team_stats(f)
players = ui.apply_player_attrs(db.player_stats(f), extra)

if teams.empty:
    st.info("No games for this selection.")
    st.stop()

# ---------- leaders (minimal: title + name) ----------
def leader(label, col, min_col=None, min_val=0):
    x = players if not min_col else players[players[min_col] >= min_val]
    if x.empty or x[col].isna().all():
        return label, "—", ""
    r = x.loc[x[col].idxmax()]
    last, _, first = ui.pname(r.player_name).partition(",")
    v = r[col]
    return label, f"{first.strip()} {last.strip()}".strip(), (f"{v:.1%}" if col.endswith("pct") else f"{v:g}")


rounds_n = f.rounds[1] - f.rounds[0] + 1
cards = [leader("Top scorer", "pts"), leader("Rebounds", "reb"), leader("Assists", "ast"),
         leader("Steals", "stl"), leader("Blocks", "blk"), leader("3-pointers", "fg3m"),
         leader("Free throws", "ft_pct", "fta", max(5, 2 * rounds_n))]
html = "".join(
    f'<div class="ldr" title="{val}"><div class="ldr-l">{lab}</div><div class="ldr-n">{name}</div></div>'
    for lab, name, val in cards)
st.markdown(f"""
<style>
.ldr-row {{display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:1.2rem; margin:.4rem 0 1.6rem}}
.ldr-l {{font-size:.72rem; letter-spacing:.08em; text-transform:uppercase; opacity:.55; margin-bottom:.15rem}}
.ldr-n {{font-size:1.02rem; font-weight:600; line-height:1.25}}
</style>
<div class="ldr-row">{html}</div>""", unsafe_allow_html=True)

# ---------- team table ----------
st.subheader("Team statistics")
mode = st.segmented_control("View", ["Totals", "Per game", "Advanced"], default="Totals", key="dash_mode")
t = teams.sort_values(["w", "net_rtg"], ascending=False)
if mode == "Totals":
    show = t[["team_name", "gp", "w", "l", "pts", "fgm", "ftm", "ft_pct", "fg2m", "fg2_pct", "fg3m", "fg3_pct",
              "oreb", "ast", "stl", "blka", "blk", "pir", "margin", "poss"]].copy()
    for c in ["ft_pct", "fg2_pct", "fg3_pct"]:
        show[c] *= 100
    st.dataframe(show, hide_index=True, width="stretch", height=740, column_config={
        "team_name": "Team", "gp": "GP", "w": "W", "l": "L", "pts": "PTS", "fgm": "FGM", "ftm": "FTM",
        "ft_pct": st.column_config.NumberColumn("FT%", format="%.2f%%"), "fg2m": "2PTM",
        "fg2_pct": st.column_config.NumberColumn("2PT%", format="%.2f%%"), "fg3m": "3PTM",
        "fg3_pct": st.column_config.NumberColumn("3PT%", format="%.2f%%"), "oreb": "OR", "ast": "AST",
        "stl": "STL", "blka": "BLKA", "blk": "BLKF", "pir": "PIR (VAL)", "margin": "+/-",
        "poss": st.column_config.NumberColumn("POSS", format="%.0f")})
elif mode == "Per game":
    keys = ["gp", "w", "l", "pts_pg", "opp_pts_pg", "margin_pg", "fg2_pct", "fg3_pct", "ft_pct", "reb_pg",
            "ast_pg", "stl_pg", "tov_pg", "blk_pg", "pir_pg"]
    ui.table(t, keys, ["team_name"], catalog=ui.TEAM, height=740)
else:
    keys = ["w", "l", "home", "away", "pace", "off_rtg", "def_rtg", "net_rtg", "efg_pct", "ts_pct", "tov_pct",
            "orb_pct", "fta_rate", "opp_efg_pct", "opp_tov_pct"]
    ui.table(t, keys, ["team_name"], catalog=ui.TEAM, height=740)

# ---------- leader boards ----------
st.subheader("Player leaders")
boards = [("PTS", ["pts"]), ("3PT%", ["fg3m", "fg3_pct"]), ("2PT%", ["fg2m", "fg2_pct"]),
          ("FT%", ["ftm", "ft_pct"]), ("Rebounds", ["dreb", "oreb", "reb"]), ("Blocks", ["blk"]),
          ("Assists", ["ast"]), ("Steals", ["stl"])]
per = st.toggle("Per game", key="dash_pg")
for row in (boards[:4], boards[4:]):
    for col, (title, keys) in zip(st.columns(4), row):
        with col:
            st.markdown(f"**{title}**")
            k = [f"{x}_pg" if per and f"{x}_pg" in players else x for x in keys]
            d = players
            if title.endswith("%"):  # need volume to rank percentages: top 40% by attempts AND a fixed minimum
                att, per_rd = {"3PT%": ("fg3a", 3), "2PT%": ("fg2a", 4), "FT%": ("fta", 4)}[title]
                d = d[(d[att] >= d[att].quantile(0.6)) & (d[att] >= per_rd * max(1, rounds_n))]
            d = d.sort_values(k[-1], ascending=False).head(25)
            ui.table(d, k, ["player_name", "team"], height=300)

# ---------- standings (built from every game played, so a half-played round is always up to date) ----------
def table_after(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values(["round", "game_date"])
    t = g.groupby("club_code").agg(GP=("wl", "size"), W=("wl", lambda x: int((x == "W").sum())),
                                   PF=("pts", "sum"), PA=("opp_pts", "sum"))
    t["L"] = t.GP - t.W
    t["diff"] = t.PF - t.PA
    rec = lambda v: g[g.venue == v].groupby("club_code").wl.agg(  # noqa: E731
        lambda x: f"{(x == 'W').sum()}-{(x == 'L').sum()}")
    t["Home"], t["Away"] = rec("H"), rec("A")
    t["Last 5"] = g.groupby("club_code").wl.agg(lambda x: "".join(x.tail(5)))
    t = t.sort_values(["W", "diff", "PF"], ascending=False)
    t["pos"] = range(1, len(t) + 1)
    return t


games_rs = db.q("""SELECT t.club_code, t.round, t.gamecode AS game_date, t.venue, t.wl, t.pts, t.opp_pts, t.phase
                   FROM fct_team_game t WHERE t.season = ?""", [f.season])
if not games_rs.empty:
    if "RS" in set(games_rs.phase):
        games_rs = games_rs[games_rs.phase == "RS"]
    last = int(games_rs["round"].max())
    cur = table_after(games_rs)
    before = table_after(games_rs[games_rs["round"] < last]) if last > 1 else pd.DataFrame()
    names = db.q("SELECT club_code, team_name FROM dim_team").set_index("club_code").team_name
    move = (cur.index.map(before.pos) - cur.pos) if len(before) else pd.Series(float("nan"), index=cur.index)
    tbl = pd.DataFrame({
        "#": cur.pos, "": [ui.arrow(v, "{:+.0f}").replace("+", "").replace("−", "") if v == v else ""
                           for v in move],
        "Team": cur.index.map(lambda c: names.get(c, c)), "GP": cur.GP, "W": cur.W, "L": cur.L,
        "Win%": (cur.W / cur.GP).map(lambda v: f"{v:.0%}"), "+/-": cur["diff"].astype(int),
        "PF": cur.PF.astype(int), "PA": cur.PA.astype(int), "Home": cur.Home.fillna("0-0"),
        "Away": cur.Away.fillna("0-0"), "Last 5": cur["Last 5"]})
    full = games_rs[games_rs["round"] == last].groupby("club_code").size().reindex(cur.index).notna().all()
    st.subheader(f"Standings — after round {last}" + ("" if full else " (in progress)"))
    st.dataframe(tbl.style.map(ui.arrow_color, subset=[""]), hide_index=True, width="stretch",
                 height=38 * (len(tbl) + 1), column_config={
                     "": st.column_config.TextColumn("", width="small", help=f"Places gained or lost in round {last}"),
                     "+/-": st.column_config.NumberColumn("+/-", format="%+d"),
                     "Last 5": st.column_config.TextColumn("Last 5", help="Newest on the right")})
    st.caption("Built from every game played, so it updates with each data refresh (also in the middle of a round). "
               "Teams level on wins are ordered by point difference; the official EuroLeague tie-break "
               "(head-to-head) can differ.")
