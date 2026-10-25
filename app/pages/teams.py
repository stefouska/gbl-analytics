import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from lib import db, fantasy, ui

ui.header("Teams", "Team efficiency, four factors and what each team gives up in fantasy")
f, _ = ui.sidebar_filters("tm")
t = db.team_stats(f)
if t.empty:
    st.info("No games for this selection.")
    st.stop()

VIEWS = {
    "Record": {"Record": ["win_pct", "home", "away", "pts_total", "opp_pts_total", "margin_pg"]},
    "Per game": {"Scoring": ["pts_pg", "opp_pts_pg", "margin_pg", "fgm_pg", "fg2m_pg", "fg3m_pg", "ftm_pg", "pir_pg"],
                 "Rebounds, passing & defense": ["oreb_pg", "dreb_pg", "reb_pg", "ast_pg", "tov_pg", "stl_pg",
                                                 "blk_pg", "blka_pg", "pf_pg", "fd_pg"]},
    "Shooting": {"Shooting": ["fg2_pct", "fg3_pct", "ft_pct", "efg_pct", "ts_pct", "fg3a_rate"]},
    "Efficiency": {"Efficiency": ["pace", "off_rtg", "def_rtg", "net_rtg", "ast_ratio"]},
    "Four factors": {"Offense": ["efg_pct", "tov_pct", "orb_pct", "fta_rate"],
                     "Defense": ["opp_efg_pct", "opp_tov_pct", "opp_orb_pct", "opp_fta_rate"]},
    "Fantasy": {"FP allowed": []},
}

tab_table, tab_profile = st.tabs(["League table", "Team profile"])
with tab_table:
    c1, c2 = st.columns([3, 2])
    view = c1.segmented_control("View", list(VIEWS), default="Efficiency", key="tm_view") or "Efficiency"
    groups = VIEWS[view]
    grp = c2.segmented_control("Group", list(groups), default=list(groups)[0], key=f"tm_grp_{view}") \
        if len(groups) > 1 else list(groups)[0]
    grp = grp or list(groups)[0]
    if view == "Fantasy":
        dvp = fantasy.defense_vs_position(f.season, f.rounds[1])
        p = dvp.pivot_table(index="club_code", columns="position", values="fp_per_player")
        d = t.set_index("club_code")[["team_name", "gp", "w", "l", "pir_pg"]].copy()
        d["opp_pir_pg"] = t.set_index("club_code").eval("op_pir / gp")
        for pos in ("G", "F", "C"):
            d[f"fp_{pos}"] = p.get(pos)
        d = d.sort_values("opp_pir_pg", ascending=False).reset_index(drop=True)
        st.dataframe(d, hide_index=True, width="stretch", height=560, column_config={
            "team_name": "Team", "gp": "GP", "w": "W", "l": "L",
            "pir_pg": st.column_config.NumberColumn("PIR/G", format="%.1f", help="Team PIR per game"),
            "opp_pir_pg": st.column_config.NumberColumn("PIR ALLOWED/G", format="%.1f",
                                                        help="PIR the opponents score against this team, per game"),
            **{f"fp_{pos}": st.column_config.NumberColumn(f"FP ALLOWED → {pos}", format="%.1f",
                                                          help=f"Average FP of an opposing {pos} player per game. "
                                                               "High = good matchup for your players")
               for pos in ("G", "F", "C")}})
        st.caption("Top rows give up the most fantasy points: pick players who face them.")
    else:
        keys = ["gp", "w", "l", *groups[grp]]
        sort = {"Record": "win_pct", "Efficiency": "net_rtg"}.get(view, keys[3])
        asc = grp == "Defense"          # lower opponent numbers = better defense
        ui.table(t.sort_values(sort, ascending=asc), keys, ["team_name"], catalog=ui.TEAM, height=560)

    c1, c2 = st.columns(2)
    with c1:
        fig = px.scatter(t, x="off_rtg", y="def_rtg", text="tag", height=460,
                         title="Offense vs defense (top-right = best)", labels={"off_rtg": "ORtg (points scored / 100)",
                                                                                 "def_rtg": "DRtg (points allowed / 100)"})
        fig.update_yaxes(autorange="reversed")
        fig.add_hline(t.def_rtg.mean(), line_dash="dot", line_color="#9ca3af")
        fig.add_vline(t.off_rtg.mean(), line_dash="dot", line_color="#9ca3af")
        fig.update_traces(textposition="top center", marker=dict(size=10, color=ui.ACCENT))
        st.plotly_chart(fig, width="stretch")
    with c2:
        pc = t.sort_values("pace")
        fig = px.bar(pc, x="pace", y="tag", orientation="h", height=460, title="Pace (possessions per 40 min)",
                     labels={"pace": "Pace", "tag": ""})
        fig.update_xaxes(range=[pc.pace.min() - 3, pc.pace.max() + 1])
        st.plotly_chart(fig, width="stretch")

with tab_profile:
    club = st.selectbox("Team", t.sort_values("team_name").club_code,
                        format_func=dict(zip(t.club_code, t.team_name)).get, key="tm_club")
    tr = t.set_index("club_code").loc[club]
    k = st.columns(4)
    k[0].metric("Record", f"{int(tr.w)}-{int(tr.l)}")
    k[1].metric("Net rating", f"{tr.net_rtg:+.1f}", f"#{int((t.net_rtg > tr.net_rtg).sum()) + 1} of {len(t)}",
                delta_color="off")
    k[2].metric("Offense (ORtg)", f"{tr.off_rtg:.1f}", f"#{int((t.off_rtg > tr.off_rtg).sum()) + 1}", delta_color="off")
    k[3].metric("Defense (DRtg)", f"{tr.def_rtg:.1f}", f"#{int((t.def_rtg < tr.def_rtg).sum()) + 1}", delta_color="off")

    tags = db.q("SELECT club_code, tag FROM dim_team").set_index("club_code").tag
    where, params = f.where("t")
    g = db.q(f"""SELECT t.*, 100*t.pts/t.poss AS ortg, 100*t.opp_pts/t.opp_poss AS drtg
                 FROM fct_team_game t WHERE {where} AND t.club_code = ? ORDER BY round""", [*params, club])
    if not g.empty:
        g["opp"] = g.opp_code.map(tags).fillna(g.opp_code)
        g["label"] = g.opp + " " + g.pts.astype(int).astype(str) + "-" + g.opp_pts.astype(int).astype(str)
        c1, c2 = st.columns(2)
        with c1:
            fig = go.Figure(go.Bar(x=g["round"], y=g.margin, text=g.label, textposition="outside",
                                   marker_color=[ui.ACCENT if m > 0 else "#dc2626" for m in g.margin]))
            fig.update_layout(title="Result by round (opponent and score)", height=360, yaxis_title="Margin")
            ui.round_axis(fig)
            fig.update_xaxes(range=[g["round"].min() - 0.6, g["round"].max() + 0.6])
            st.plotly_chart(fig, width="stretch")
        with c2:
            fig = go.Figure()
            fig.add_scatter(x=g["round"], y=g.ortg, name="ORtg (scored)", mode="lines+markers", line_color=ui.ACCENT)
            fig.add_scatter(x=g["round"], y=g.drtg, name="DRtg (allowed)", mode="lines+markers", line_color="#dc2626")
            fig.update_layout(title="Points scored and allowed per 100 possessions", height=360,
                              legend_orientation="h")
            ui.round_axis(fig)
            fig.update_xaxes(range=[g["round"].min() - 0.6, g["round"].max() + 0.6])
            st.plotly_chart(fig, width="stretch")
        split = db.team_stats(db.Filters(**{**f.__dict__, "clubs": [club], "venue": "H"}))
        split_a = db.team_stats(db.Filters(**{**f.__dict__, "clubs": [club], "venue": "A"}))
        parts = [x for x in (split.assign(split="Home"), split_a.assign(split="Away")) if not x.empty]
        if parts:
            st.markdown("**Home / away**")
            ui.table(pd.concat(parts), ["gp", "w", "l", "pts_pg", "opp_pts_pg", "off_rtg", "def_rtg", "efg_pct",
                                        "tov_pct", "orb_pct"], ["split"], catalog=ui.TEAM, height=110)
        st.markdown("**Players**")
        p = db.player_stats(db.Filters(**{**f.__dict__, "clubs": [club]}))
        ui.table(p.sort_values("pir", ascending=False),
                 ["gp", "min_pg", "pts_pg", "reb_pg", "ast_pg", "pir_pg", "ts_pct", "usg_pct"],
                 ["player_name", "position"], height=420)
