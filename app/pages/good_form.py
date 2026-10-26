"""Good form: the players who have been excellent in their last 2-3 games — and why."""
import pandas as pd
import plotly.express as px
import streamlit as st

from lib import common, labels, ui

st.title("Good form")
st.caption("Who is on fire right now. Good score 0-100 (higher = better) over each player's last 3 games, ranked "
           "against players of the same position.")

season, as_of = common.season_and_round("gf")
b = common.board(season, as_of)
if b.empty or "good_score" not in b:
    st.info("Not enough games yet.")
    st.stop()

c1, c2, c3, c4 = st.columns([1.2, 1.4, 1, 1.2])
pos = c1.segmented_control("Position", ["All", "G", "F", "C"], default="All", key="gf_pos") or "All"
teams = c2.multiselect("Teams", sorted(b.team.dropna().unique()), key="gf_teams")
max_p = c3.number_input("Max price", 4.0, 30.0, 30.0, 0.5, key="gf_price")
only_good = c4.toggle("Only 🔥 / 🟢", True, key="gf_only")
hide_inj = st.toggle("Hide injured / doubtful", True, key="gf_inj")
d = b[b.good_score.notna() & (b.price.fillna(0) <= max_p)]
if pos != "All":
    d = d[d.position == pos]
if teams:
    d = d[d.team.isin(teams)]
if only_good:
    d = d[d.good_score >= 65]
if hide_inj:
    d = d[~d.unavailable]
d = d.assign(fpg_per_credit=d.fp_win_pg / d.price, fpg_l3_per_credit=d.good_fp / d.price)
d = d.sort_values("good_score", ascending=False)

k = st.columns(3)
k[0].metric("🔥 Hot", int((d.good_score >= 80).sum()))
k[1].metric("🟢 Good", int(((d.good_score >= 65) & (d.good_score < 80)).sum()))
k[2].metric("Players shown", len(d))

COLS = ["player_name", "team", "position", "price", "fpg_per_credit", "fpg_l3_per_credit", "good_score", "good_label", "good_reasons", "good_fp",
        "good_over_be", "good_jump", "good_pts30", "good_astreb30", "good_ts", "good_min_jump", "exp_fp_next",
        "next_opp", "selected_pct", "inj_flag"]
labels.show(d, [c for c in COLS if c in d], height=520, drop_empty=True,
            extra={"fpg_per_credit": ("FP/G PER CREDIT", "Official fantasy points per game this season "
                                      "(win bonus included) ÷ price", "d2"),
                   "fpg_l3_per_credit": ("FP L3 PER CREDIT", "Fantasy points per game in the last 3 ÷ price", "d2")})

top = d.head(25)
if len(top):
    t = top.assign(name=top.player_name.map(ui.pname))
    fig = px.scatter(t, x="price", y="good_fp", size="good_score", color="position", hover_name="name",
                     text="name", height=480, labels={"price": "Price", "good_fp": "FP per game, last 3"},
                     title="In form vs price (the line is the break-even: above it the price goes up)")
    xs = pd.Series([t.price.min() - 0.5, t.price.max() + 0.5])
    fig.add_scatter(x=xs, y=1.1 * xs, mode="lines", line=dict(dash="dot", color="#9ca3af"), name="break-even",
                    hoverinfo="skip")
    fig.update_traces(textposition="top center", textfont_size=10, selector=dict(mode="markers+text"))
    st.plotly_chart(fig, width="stretch")

with st.expander("How the good score works"):
    st.markdown(
        "- Over each player's **last 3 games** (at least 2 of them, 15+ minutes a game).\n"
        "- Ranked **against players of the same position**: fantasy points per game 30% · points above his "
        "break-even (1.1 × price) 20% · jump vs his season 15% · points per 30 min 10% · assists + rebounds per 30 "
        "min 10% · true shooting 10% · more minutes than usual 5%.\n"
        "- 🔥 **Hot** = 80+ · 🟢 **Good** = 65-80. Its mirror is the **Bad form** page.")
