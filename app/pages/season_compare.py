"""This season vs last season: players, teams, standings at the same point, league-wide trends."""
import pandas as pd
import plotly.express as px
import streamlit as st

from lib import db, fantasy, ui

ui.header("Season comparison", "This season against last season — players, teams, standings and league trends")
seasons = ui._seasons()
if len(seasons) < 2:
    st.info("Only one season in the data. Add last season to config.SEASONS and run the refresh with 'full'.")
    st.stop()
now_s = st.sidebar.selectbox("Season", [s for s in seasons if s - 1 in seasons],
                             format_func=lambda s: f"{s}-{str(s + 1)[-2:]} vs {s - 1}-{str(s)[-2:]}", key="sc_s")
last_s = now_s - 1
rounds_now = ui._rounds(now_s)
n = max(rounds_now) if rounds_now else 0
same = st.sidebar.toggle(f"Last season: only rounds 1-{n}", False, key="sc_same",
                         help="Off = last season in full (more reliable averages). On = the same point of the season "
                              "(like for like)")
ui.footer()
lab = lambda s: f"{s}-{str(s + 1)[-2:]}"  # noqa: E731
F_now = db.Filters(season=now_s)
F_last = db.Filters(season=last_s, rounds=(1, n) if same else (1, 99))
st.caption(f"**{lab(now_s)}**: rounds 1-{n} · **{lab(last_s)}**: {'rounds 1-' + str(n) if same else 'full season'}")

LOWER_IS_BETTER = {"def_rtg", "opp_pts_pg", "tov_pct", "tov_pg", "opp_efg_pct"}


def delta_frame(now, last, key, keys, fmt, id_cols):
    """now/last/Δ columns for each metric, Δ as ▲/▼ coloured green when it improved."""
    m = now.merge(last, on=key, suffixes=("", "_last"), how="inner")
    out = m[id_cols].copy()
    for c in ("gp", "w", "l"):
        if c in out:
            out[c] = out[c].fillna(0).astype(int)
    colors = {}
    for k, title in keys.items():
        pct = fmt.get(k) == "pct"
        mul = 100 if pct else 1
        out[f"{title}"] = m[k] * mul
        out[f"{title} ({lab(last_s)[:4]})"] = m[f"{k}_last"] * mul
        d = (m[k] - m[f"{k}_last"]) * mul
        out[f"Δ {title}"] = d.map(lambda v: ui.arrow(v))
        colors[f"Δ {title}"] = k in LOWER_IS_BETTER
    return m, out, colors


def style(df, colors, num_fmt="{:.1f}"):
    sty = df.style.format({c: num_fmt for c in df.columns if df[c].dtype.kind == "f"})
    for c, inverse in colors.items():
        def col(v, inverse=inverse):
            if not isinstance(v, str) or not v[:1] in "▲▼":
                return ""
            good = (v[0] == "▲") != inverse
            return "color: #15803d; font-weight: 600" if good else "color: #dc2626; font-weight: 600"
        sty = sty.map(col, subset=[c])
    return sty


t1, t2, t0, t3, t4 = st.tabs(["👤 Players", "🛡️ Teams", "🔁 Roster changes", "🏆 Standings", "📈 League trends"])
rc = fantasy.roster_changes(now_s)
prior = fantasy._last_season_prior(now_s)

# ---------------------------------------------------------------- players
with t1:
    PM = {"fp_pg": "FP/G", "pir_pg": "PIR/G", "pts_pg": "PTS/G", "reb_pg": "TR/G", "ast_pg": "AST/G",
          "min_pg": "MIN/G", "ts_pct": "TS%", "usg_pct": "USG%", "fg3_pct": "3PT%"}
    pn, pl = db.player_stats(F_now), db.player_stats(F_last)
    c1, c2, c3, c4 = st.columns([2, 3, 1, 1])
    search = c1.text_input("Search player", key="sc_search")
    pick = c2.multiselect("Metrics", list(PM), default=["fp_pg", "pir_pg", "min_pg"], format_func=PM.get,
                          key="sc_pm", max_selections=4)
    min_gp_last = c3.number_input(f"Min games {lab(last_s)[:4]}", 0, 40, 10, key="sc_gpl")
    moved = c4.toggle("Changed team", False, key="sc_moved")
    pl = pl[pl.gp >= min_gp_last]
    m, out, colors = delta_frame(
        pn, pl[["player_id", "team", *pick]].rename(columns={"team": "team_prev"}),
        "player_id", {k: PM[k] for k in pick}, {k: ui.PLAYER[k].fmt for k in pick if k in ui.PLAYER},
        ["player_name", "team", "position", "gp"])
    out.insert(2, f"Team {lab(last_s)[:4]}", m.team_prev)
    out["player_name"] = out.player_name.map(ui.pname)
    if pick:
        out["_d"] = m[pick[0]] - m[f"{pick[0]}_last"]
        out = out.sort_values("_d", ascending=False).drop(columns="_d")
        st.caption(f"Sorted by the change in {PM[pick[0]]} (biggest improvement first) · only players in both seasons")
    if moved:
        out = out[out.team != out[f"Team {lab(last_s)[:4]}"]]
    if search:
        out = out[out.player_name.str.contains(search, case=False, na=False)]
    st.dataframe(style(out, colors), hide_index=True, width="stretch", height=620,
                 column_config={"player_name": "Player", "team": "Team", "position": "Pos", "gp": "GP"})
    new = pn[~pn.player_id.isin(db.player_stats(db.Filters(season=last_s)).player_id)]
    with st.expander(f"New in the EuroLeague this season ({len(new)} players)"):
        ui.table(new.sort_values("pir_pg", ascending=False), ["gp", "min_pg", "pts_pg", "reb_pg", "ast_pg", "pir_pg"],
                 ["player_name", "team", "position"], height=400)

# ---------------------------------------------------------------- teams
with t2:
    TM = {"win_pct": "W%", "net_rtg": "Net", "off_rtg": "ORtg", "def_rtg": "DRtg", "pace": "Pace",
          "pts_pg": "PTS/G", "efg_pct": "eFG%", "tov_pct": "TOV%", "fg3a_rate": "3PA rate"}
    tn, tl = db.team_stats(F_now), db.team_stats(F_last)
    pick = st.multiselect("Metrics", list(TM), default=["net_rtg", "off_rtg", "def_rtg", "pace"], format_func=TM.get,
                          key="sc_tm", max_selections=5)
    m, out, colors = delta_frame(tn, tl[["club_code", *pick]], "club_code", {k: TM[k] for k in pick},
                                 {k: ui.TEAM[k].fmt for k in pick if k in ui.TEAM}, ["team_name", "gp", "w", "l"])
    if not rc.empty:
        out.insert(1, "Kept %", m.club_code.map(rc.continuity) * 100)
    if "net_rtg" in m:
        out = out.assign(_d=m.net_rtg - m.net_rtg_last).sort_values("_d", ascending=False).drop(columns="_d")
    st.dataframe(style(out, colors), hide_index=True, width="stretch", height=38 * (len(out) + 1),
                 column_config={"team_name": "Team", "gp": "GP", "w": "W", "l": "L",
                                "Kept %": st.column_config.NumberColumn(
                                    "Kept %", format="%.0f%%",
                                    help="Share of last season's minutes played by players still on the roster. "
                                         "Low = a new team: last season says less about it")})
    st.caption("Green = better than last season (for DRtg and TOV% lower is better). New teams have no row.")
    if {"net_rtg"} <= set(m.columns):
        fig = px.scatter(m, x="net_rtg_last", y="net_rtg", text="tag", height=460,
                         labels={"net_rtg_last": f"Net rating {lab(last_s)}", "net_rtg": f"Net rating {lab(now_s)}"},
                         title="Net rating: last season vs now (above the line = better than last season)")
        lo, hi = min(m.net_rtg.min(), m.net_rtg_last.min()) - 2, max(m.net_rtg.max(), m.net_rtg_last.max()) + 2
        fig.add_scatter(x=[lo, hi], y=[lo, hi], mode="lines", line=dict(dash="dot", color="#9ca3af"), showlegend=False)
        fig.update_traces(textposition="top center", marker=dict(size=10, color=ui.ACCENT), selector=dict(mode="markers+text"))
        st.plotly_chart(fig, width="stretch")
        st.caption("Early in the season a few games move the vertical position a lot.")

# ---------------------------------------------------------------- roster changes
with t0:
    if rc.empty:
        st.info("No roster data.")
    else:
        names = db.q("SELECT club_code, team_name FROM dim_team").set_index("club_code").team_name
        tb = pd.DataFrame({"Team": rc.index.map(lambda c: names.get(c, c)),
                           "Kept %": rc.continuity * 100, "Arrivals": rc.n_arrivals, "Departures": rc.n_departures,
                           "Roster value": rc.roster_value,
                           "Expected level": (prior.ortg_gap - prior.drtg_gap).reindex(rc.index)},
                          index=rc.index).sort_values("Expected level", ascending=False)
        st.dataframe(tb, hide_index=True, width="stretch", height=38 * (len(tb) + 1), column_config={
            "Kept %": st.column_config.ProgressColumn("Kept %", min_value=0, max_value=100, format="%.0f%%",
                                                      help="Share of last season's minutes still on the roster"),
            "Roster value": st.column_config.NumberColumn("Roster value", format="%.1f",
                                                          help="Sum of the 8 highest pre-season fantasy prices"),
            "Expected level": st.column_config.NumberColumn(
                "Starting level", format="%+.1f",
                help="Net rating (points per 100 possessions vs the average) the predictions start from")})
        st.caption("How the predictions start the season: players who stayed carry last season's level, the rest of "
                   "the team is judged by the roster value on paper — mixed by 'Kept %'. After a few games this "
                   "season's own results take over.")
        club = st.selectbox("Team details", tb.index, format_func=lambda c: names.get(c, c), key="sc_rc")
        a, d = rc.loc[club, "arrivals"], rc.loc[club, "departures"]
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**➕ Arrivals ({len(a)})**")
            if len(a):
                st.dataframe(a.assign(player_name=a.player_name.map(ui.pname))[["player_name", "club_code", "pir_pg"]]
                             .sort_values("pir_pg", ascending=False), hide_index=True, width="stretch",
                             column_config={"player_name": "Player", "club_code": "Came from",
                                            "pir_pg": st.column_config.NumberColumn(f"PIR/G {lab(last_s)[:4]}",
                                                                                    format="%.1f")})
        with c2:
            st.markdown(f"**➖ Departures ({len(d)})**")
            if len(d):
                st.dataframe(d.assign(player_name=d.player_name.map(ui.pname), mpg=d.minutes / d.gp)[
                    ["player_name", "gp", "mpg", "pir_pg"]], hide_index=True, width="stretch",
                    column_config={"player_name": "Player", "gp": "GP",
                                   "mpg": st.column_config.NumberColumn("MIN/G", format="%.1f"),
                                   "pir_pg": st.column_config.NumberColumn("PIR/G", format="%.1f")})
        st.caption("Came from = his club last season in the EuroLeague (empty = from another league).")

# ---------------------------------------------------------------- standings at the same round
with t3:
    s = db.q("""SELECT s.season, s.round, s."club.code" AS club_code, s.position, s.gamesWon AS w, s.gamesLost AS l,
                       s.pointsDifference AS diff, dt.team_name
                FROM fct_standings s LEFT JOIN dim_team dt ON dt.club_code = s."club.code"
                WHERE s.season IN (?, ?)""", [now_s, last_s])
    if s.empty or not n:
        st.info("No standings yet.")
    else:
        rn = int(s[s.season == now_s]["round"].max())
        cur = s[(s.season == now_s) & (s["round"] == rn)]
        prev = s[(s.season == last_s) & (s["round"] == rn)].set_index("club_code")
        fin = s[s.season == last_s]
        fin = fin[fin["round"] == fin["round"].max()].set_index("club_code")
        tbl = pd.DataFrame({
            "#": cur.position, "Team": cur.team_name, "W-L": cur.w.astype(str) + "-" + cur.l.astype(str),
            "+/-": cur["diff"],
            f"# {lab(last_s)[:4]} after R{rn}": cur.club_code.map(prev.position),
            f"W-L {lab(last_s)[:4]} after R{rn}": cur.club_code.map(prev.w.astype(str) + "-" + prev.l.astype(str)),
            f"Final # {lab(last_s)[:4]}": cur.club_code.map(fin.position)}).sort_values("#")
        tbl["vs last year"] = (tbl[f"# {lab(last_s)[:4]} after R{rn}"] - tbl["#"]).map(
            lambda v: ui.arrow(v, "{:+.0f}").replace("+", "") if v == v else "new")
        st.dataframe(tbl.style.map(ui.arrow_color, subset=["vs last year"]).format(
            {c: "{:.0f}" for c in tbl.columns if tbl[c].dtype.kind == "f"}, na_rep="—"),
            hide_index=True, width="stretch", height=38 * (len(tbl) + 1))
        st.caption(f"After round {rn} this season compared with after round {rn} last season. "
                   "▲ = higher in the table than at the same point last year.")

# ---------------------------------------------------------------- league trends
with t4:
    q = """SELECT season, count(*) / 2 AS games, avg(pts) AS pts, avg((poss + opp_poss) / 2 * 200 / minutes) AS pace,
                  sum(fg3a) / sum(fga) AS fg3a_rate, sum(fta) / sum(fga) AS fta_rate,
                  (sum(fgm) + 0.5 * sum(fg3m)) / sum(fga) AS efg, sum(tov) / sum(poss) AS tov_pct,
                  sum(pts) / sum(poss) * 100 AS ortg, avg(CASE WHEN venue = 'H' THEN CASE WHEN wl = 'W' THEN 1 ELSE 0 END END) AS home_win
           FROM fct_team_game WHERE (season = ? AND round <= ?) OR (season = ? AND round <= ?) GROUP BY 1"""
    lt = db.q(q, [now_s, n or 99, last_s, n if same else 99]).set_index("season")
    if len(lt) == 2:
        rows = [("Points per team per game", "pts", "{:.1f}"), ("Pace (possessions / 40 min)", "pace", "{:.1f}"),
                ("Points per 100 possessions", "ortg", "{:.1f}"), ("3PA share of shots", "fg3a_rate", "{:.1%}"),
                ("Free throws per shot (FT rate)", "fta_rate", "{:.2f}"), ("eFG%", "efg", "{:.1%}"),
                ("Turnovers per possession", "tov_pct", "{:.1%}"), ("Home teams win", "home_win", "{:.0%}")]
        k = st.columns(4)
        for i, (title, col, f) in enumerate(rows):
            v, lv = lt.at[now_s, col], lt.at[last_s, col]
            d = v - lv
            dtxt = f"{d * 100:+.1f} pts" if f.endswith("%}") else ("{:+" + f[2:]).format(d)
            k[i % 4].metric(title, f.format(v), f"{dtxt} vs {f.format(lv)}",
                            delta_color="inverse" if col == "tov_pct" else "normal")
        st.caption(f"League-wide averages. {lab(now_s)}: {int(lt.at[now_s, 'games'])} games · {lab(last_s)}: "
                   f"{int(lt.at[last_s, 'games'])} games. Early in the season these move a lot.")
