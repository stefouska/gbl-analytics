"""Bad form: players who are hurting fantasy teams — missed free throws and shots, fouls, turnovers, few assists,
few points. The same 'bad score' is used by the Lineup optimizer."""
import pandas as pd
import plotly.express as px
import streamlit as st

from lib import common, fantasy, labels, ui

st.title("Bad form")
st.caption("Who is underperforming — and why. Bad score 0-100 (higher = worse), per 30 minutes and against "
           "players of the same position: 60% the last 5 games, 40% the season.")

season, as_of = common.season_and_round("bf")
b = common.board(season, as_of)
if b.empty or "bad_score" not in b:
    st.info("Not enough games yet.")
    st.stop()

c1, c2, c3, c4 = st.columns([1.2, 1, 1, 1.2])
pos = c1.segmented_control("Position", ["All", "G", "F", "C"], default="All", key="bf_pos") or "All"
min_m = c2.number_input("Min. minutes / game", 0.0, 40.0, 12.0, 1.0, key="bf_min")
max_p = c3.number_input("Max price", 4.0, 30.0, 30.0, 0.5, key="bf_price")
only_bad = c4.toggle("Only 🔴 / 🟠", True, key="bf_only")
d = b[b.bad_score.notna() & (b.min_pg >= min_m) & (b.price.fillna(0) <= max_p)]
if pos != "All":
    d = d[d.position == pos]
if only_bad:
    d = d[d.bad_score >= 60]
d = d.sort_values("bad_score", ascending=False)

k = st.columns(3)
k[0].metric("🔴 Bad form", int((d.bad_score >= 70).sum()))
k[1].metric("🟠 Watch", int(((d.bad_score >= 60) & (d.bad_score < 70)).sum()))
k[2].metric("Players shown", len(d))

COLS = ["player_name", "team", "position", "price", "min_pg", "bad_score", "bad_label", "bad_reasons", "bad_recent",
        "bad_season", "bad_miss_ft", "bad_miss_fg", "bad_pf", "bad_tov", "bad_ast", "bad_pts", "fp_l3", "exp_fp_next"]
labels.show(d, [c for c in COLS if c in d], height=520)

top = d.head(15)
if len(top):
    parts = pd.DataFrame({
        "player": top.player_name.map(ui.pname),
        "Missed FT": top.bad_miss_ft, "Missed shots": top.bad_miss_fg, "Fouls": top.bad_pf, "Turnovers": top.bad_tov})
    fig = px.bar(parts.melt("player", var_name="what", value_name="per 30 min"), x="per 30 min", y="player",
                 color="what", orientation="h", height=460, title="Where the damage comes from (last 5, per 30 min)")
    fig.update_yaxes(categoryorder="array", categoryarray=list(parts.player[::-1]), title=None)
    fig.update_layout(legend_title=None, legend_orientation="h")
    st.plotly_chart(fig, width="stretch")

with st.expander("How the bad score works"):
    st.markdown(
        "- For every player with enough minutes: **missed free throws, missed field goals, fouls, turnovers, assists "
        "and points per 30 minutes**.\n"
        "- Each is ranked **against players of the same position** (a center with few assists is normal; "
        "a guard with few assists is not).\n"
        "- Weights: turnovers 20% · few points 20% · missed FT 15% · missed shots 15% · fouls 15% · few assists 15%.\n"
        "- **60% last 5 games + 40% season** → 0-100. 🔴 70+ bad form · 🟠 60-70 watch · 🟢 below 60.\n"
        "- The **Lineup optimizer** can use it: with *Penalise bad form* on, a player's expected points are cut by up "
        "to 20% (bad score 60 → 0%, 100 → −20%).")
