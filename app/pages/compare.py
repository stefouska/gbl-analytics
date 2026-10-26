import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib import common, fantasy, labels, ui

ui.header("Compare players", "Up to 4 players side by side · the best value in each row is green")
season, as_of = common.season_and_round("cmp")
b = common.board(season, as_of)
if b.empty:
    st.info("No games yet.")
    st.stop()
names = common.name_map(b)
default = list(b.nlargest(2, "exp_fp_next").player_id)
sel = st.multiselect("Players", b.sort_values("fp_pg", ascending=False).player_id, default=default,
                     format_func=names.get, max_selections=4)
if not sel:
    st.stop()
d = b.set_index("player_id").loc[sel]
cols = [ui.pname(n) for n in d.player_name]

# player cards
for c, pid in zip(st.columns(len(sel)), sel):
    r = d.loc[pid]
    status = {"OUT": "⛔ Out", "Q": "🟠 Doubtful"}.get(r.inj_flag, "🟢 Available")
    c.markdown(f"**{ui.pname(r.player_name)}**  \n{r.team} · {r.position or '—'} · {r.price:.1f} cr · {status}")

# (label, column, format, better) — better: 'high' | 'low' | None (no highlight)
SECTIONS = {
    "Fantasy": [("Expected FP next", "exp_fp_next", "{:.1f}", "high"), ("FP / game", "fp_pg", "{:.1f}", "high"),
                ("FP last 3", "fp_l3", "{:.1f}", "high"), ("Value (FP L3 / credit)", "value_l3", "{:.2f}", "high"),
                ("Pick score", "pick_score", "{:.0f}", "high"),
                ("Expected price move", "exp_price_move", "{:+.1f}", "high")],
    "Playing time": [("Minutes / game", "min_pg", "{:.1f}", "high"), ("Games", "gp", "{:.0f}", None),
                     ("Safety (0-100)", "risk_score", "{:.0f}", "high")],
    "Next game": [("Opponent", "next_opp", None, None), ("Matchup", "schedule_band", None, None),
                  ("Team win chance", "next_win_prob", "{:.0%}", "high")],
}


def section(rows):
    raw = pd.DataFrame({lab: [d.loc[pid, col] for pid in sel] for lab, col, _, _ in rows}, index=cols).T
    if "Opponent" in raw.index:
        raw.loc["Opponent"] = [("vs " if d.loc[p, "next_venue"] == "H" else "@ ") + str(d.loc[p, "next_opp"])
                               if isinstance(d.loc[p, "next_opp"], str) else "—" for p in sel]
    shown = raw.copy().astype(object)
    style = pd.DataFrame("", index=raw.index, columns=raw.columns)
    for lab, col, fmt, better in rows:
        vals = pd.to_numeric(raw.loc[lab], errors="coerce")
        if fmt:
            shown.loc[lab] = [fmt.format(v) if v == v else "—" for v in vals]
        if better and len(sel) > 1 and vals.notna().sum() > 1 and vals.max() != vals.min():
            best = vals.idxmax() if better == "high" else vals.idxmin()
            style.loc[lab, best] = "color: #15803d; font-weight: 700"
    if "Matchup" in raw.index:
        for c in raw.columns:
            style.loc["Matchup", c] = labels.BAND_COLORS.get(raw.loc["Matchup", c], "")
    return shown.fillna("—").style.apply(lambda _: style, axis=None)


for title, rows in SECTIONS.items():
    st.markdown(f"**{title}**")
    st.dataframe(section(rows), width="stretch", height=36 * (len(rows) + 1) + 2)

c1, c2 = st.columns(2)
with c1:
    axes = {"exp_fp_next": "Exp FP", "fp_l3": "FP L3", "fp_pg": "FP/G", "value_l3": "Value",
            "risk_score": "Safety", "min_pg": "Minutes", "usg_season": "Usage"}
    elig = b[b.eligible]
    fig = go.Figure()
    for pid in sel:
        r = [((elig[a] <= d.loc[pid, a]).mean() * 100) if d.loc[pid, a] == d.loc[pid, a] else 0 for a in axes]
        fig.add_scatterpolar(r=r + r[:1], theta=list(axes.values()) + [list(axes.values())[0]], fill="toself",
                             name=ui.pname(d.loc[pid, "player_name"]), opacity=0.6)
    fig.update_layout(title="Percentile among eligible players", height=400, legend_orientation="h",
                      polar=dict(radialaxis=dict(range=[0, 100], showticklabels=False)))
    st.plotly_chart(fig, width="stretch")
with c2:
    o = fantasy.team_outlook(season, as_of, 3)
    if not o.empty:
        st.markdown("**Next 3 games**")
        rows = []
        for pid in sel:
            t = o[o.club_code == d.loc[pid, "club_code"]].sort_values("round")
            rows.append({"Player": ui.pname(d.loc[pid, "player_name"]),
                         **{f"R{int(r['round'])}": f"{r.label} · {r.opp_drtg:.0f}" for _, r in t.iterrows()}})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Number = opponent's points allowed per 100 possessions (higher = weaker defense = easier).")
