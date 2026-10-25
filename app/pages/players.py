import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib import common, db, fantasy, ui

ui.header("Players", "League table and player profiles")
f, extra = ui.sidebar_filters("pl", with_player_attrs=True)
df = ui.apply_player_attrs(db.player_stats(f), extra)
if df.empty:
    st.info("No players for this selection.")
    st.stop()

# fantasy numbers (price, expected FP...) of the latest round, merged by player
rnds = ui._rounds(f.season)
GBL = os.getenv("LEAGUE") == "GBL"                  # the GBL app has no fantasy game
board = common.board(f.season, max(rnds)) if rnds and not GBL else pd.DataFrame()
if not board.empty:
    df = df.merge(board[["player_id", "price", "exp_fp_next", "pick_score", "fp_l3", "value_l3"]],
                  on="player_id", how="left")

tab_table, tab_profile = st.tabs(["League table", "Player profile"])

# ---------------------------------------------------------------- league table
# Every view = fixed player columns + one small, titled group of stats (no side-scrolling)
VIEWS = {
    "Stats": {
        "Scoring": (["pts", "fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "pir"],
                    ["pts_pg", "fg2m_pg", "fg3m_pg", "ftm_pg", "fga_pg", "fta_pg", "missed_fg_pg", "pir_pg"]),
        "Rebounds & playmaking": (["oreb", "dreb", "reb", "ast", "tov"],
                                  ["oreb_pg", "dreb_pg", "reb_pg", "ast_pg", "tov_pg", "ball_handling"]),
        "Defense & fouls": (["stl", "blk", "blka", "pf", "fd"], ["stl_pg", "blk_pg", "pf_pg", "fd_pg"]),
    },
    "Shooting": {
        "Percentages": ["fg2_pct", "fg2a", "fg3_pct", "fg3a", "ft_pct", "fta", "efg_pct", "ts_pct"],
        "Shot profile": ["fga_pg", "fg3a_rate", "fta_rate", "pts_per_min", "pts_pg"],
    },
    "Advanced": {
        "Usage & efficiency": ["usg_pct", "ts_pct", "off_rtg", "net_rtg_ind", "eff", "uper"],
        "Playmaking": ["ast_pct", "tov_pct", "ast_to", "ast_pg", "tov_pg"],
        "Rebounding": ["orb_pct", "drb_pct", "trb_pct", "reb_pg"],
        "Defense": ["stl_pct", "blk_pct", "team_drtg_on", "stl_pg", "blk_pg"],
    },
    "Fantasy": {"Fantasy": ["fp", "fp_pg", "fp_win_pg", "fp_l3", "price", "value_l3", "exp_fp_next", "pick_score"]},
}
if os.getenv("LEAGUE") == "GBL":
    VIEWS.pop("Fantasy")
FANTASY_COLS = {"price": ("PRICE", "%.1f", "Current fantasy price"),
                "exp_fp_next": ("EXP FP NEXT", "%.1f", "Expected fantasy points next game (see Picks)"),
                "pick_score": ("PICK SCORE", "%d", "0-100 pick score (see Picks)"),
                "fp_l3": ("FP L3", "%.1f", "Average fantasy points, last 3 games"),
                "value_l3": ("FP L3 / CR", "%.2f", "Fantasy points (last 3) per credit")}

with tab_table:
    c1, c2, c3 = st.columns([3, 2, 1])
    opts = df.sort_values("player_name").drop_duplicates("player_id")
    lab_p = dict(zip(opts.player_id, opts.player_name.map(ui.pname) + " · " + opts.team.astype(str)))
    picked = c1.multiselect("Players", list(lab_p), format_func=lab_p.get, key="pl_pick",
                            placeholder="All players — type to pick one or more",
                            help="Pick one or more players to compare them in the table (the minutes filter is "
                                 "ignored for them)")
    view = c2.segmented_control("View", list(VIEWS), default="Stats", key="pl_view") or "Stats"
    min_mpg = c3.number_input("Min MIN/G", 0.0, 40.0, 10.0, 1.0, key="pl_minmpg",
                              help="Hide players with fewer minutes per game (small samples give extreme %)")
    groups = VIEWS[view]
    c1, c2 = st.columns([3, 1])
    grp = c1.segmented_control("Group", list(groups), default=list(groups)[0], key=f"pl_grp_{view}") \
        or list(groups)[0]
    keys = groups[grp]
    per_game = True
    if view == "Stats":
        per_game = c2.toggle("Per game", True, key="pl_pg")
        keys = keys[1] if per_game else keys[0]
    keys = [k for k in keys if k in df]

    if picked:
        d = df[df.player_id.isin(picked)]
    else:
        d = df[df.min_pg >= min_mpg] if min_mpg else df
    d = d.sort_values(keys[0], ascending=False)
    fixed = ["gp", "min_pg"]
    fp_key = "fp" if (view == "Stats" and not per_game) else "fp_pg"     # fantasy points always on show
    if view != "Fantasy" and fp_key in d and fp_key not in keys:
        fixed.append(fp_key)
    show = ui.pct_to_100(d, fixed + keys)[["player_name", "team", "position", *fixed, *keys]]
    show = show.assign(player_name=show.player_name.map(ui.pname))
    cfg = {c: st.column_config.TextColumn(ui.ID_LABELS[c]) for c in ["player_name", "team", "position"]}
    cfg.update(ui.column_config(fixed + keys))
    for k, (lab, fmt, h) in FANTASY_COLS.items():
        cfg[k] = st.column_config.NumberColumn(lab, format=fmt, help=h)
    st.dataframe(show, column_config=cfg, hide_index=True, height=640, width="stretch")
    first = ui.PLAYER[keys[0]].label if keys[0] in ui.PLAYER else FANTASY_COLS.get(keys[0], (keys[0],))[0]
    st.caption(f"{len(d)} players · sorted by {first} · click a column title to sort · hover a title for its meaning")
    st.download_button("Download CSV", d.to_csv(index=False), "players.csv", "text/csv")

def _age(birth) -> str:
    b = pd.to_datetime(birth, errors="coerce")
    if pd.isna(b):
        return ""
    t = pd.Timestamp.today()
    return f"{t.year - b.year - ((t.month, t.day) < (b.month, b.day))} years"


def player_card(pid, r, season, teams):
    """Player presentation: photo, bio, where he played last season and his EuroLeague seasons in the data."""
    names = dict(zip(teams.club_code, teams.team_name))
    bio = db.q("SELECT * FROM raw_players WHERE player_id = ? ORDER BY season DESC LIMIT 1", [pid])
    bio = bio.iloc[0] if len(bio) else pd.Series(dtype=object)
    val = lambda k: bio.get(k) if k in bio and pd.notna(bio.get(k)) and bio.get(k) != "" else None  # noqa: E731
    career = db.q("""SELECT p.season, p.club_code, count(*) AS gp, avg(p.minutes) AS mpg, avg(p.pts) AS pts,
                            avg(p.reb) AS reb, avg(p.ast) AS ast, avg(p.pir) AS pir, avg(p.fp_win) AS fp
                     FROM fct_player_game p WHERE p.player_id = ? AND p.minutes > 0
                     GROUP BY 1, 2 ORDER BY 1, min(p.round)""", [pid])
    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 3, 2])
        with c1:
            if val("headshot"):
                st.image(val("headshot"), width=130)
            else:
                ini = "".join(w[0] for w in ui.pname(r.player_name).replace(",", "").split()[:2][::-1]).upper()
                st.markdown(f"<div style='width:110px;height:110px;border-radius:55px;background:{ui.ACCENT};"
                            f"color:white;display:flex;align-items:center;justify-content:center;font-size:2.4rem;"
                            f"font-weight:700'>{ini}</div>", unsafe_allow_html=True)
        with c2:
            last, _, first = ui.pname(r.player_name).partition(",")
            num = f"#{val('dorsal')} · " if val("dorsal") else ""
            st.markdown(f"## {first.strip()} {last.strip()}")
            pos = {"G": "Guard", "F": "Forward", "C": "Center"}.get(r.position, "—") if pd.notna(r.position) else "—"
            st.markdown(f"**{num}{names.get(r.club_code, r.team)}** · {pos}")
            facts = []
            born = pd.to_datetime(val("birth_date"), errors="coerce")
            if pd.notna(born):
                facts.append(f"🎂 {_age(born)} (born {born:%d/%m/%Y}"
                             + (f", {val('birth_country')})" if val("birth_country") else ")"))
            if val("nationality"):
                facts.append(f"🌍 {val('nationality')}")
            if val("height_cm"):
                facts.append(f"📏 {int(float(val('height_cm')))} cm")
            if val("weight_kg"):
                facts.append(f"⚖️ {int(float(val('weight_kg')))} kg")
            since = pd.to_datetime(val("since"), errors="coerce")
            if pd.notna(since):
                facts.append(f"🗓️ at the club since {since:%B %Y}")
            st.markdown("  \n".join(facts) if facts else "_No bio data from the EuroLeague for this player._")
        with c3:
            prev = career[career.season == season - 1]
            st.markdown(f"**Last season ({season - 1}-{str(season)[-2:]})**")
            if len(prev):
                clubs = [names.get(c, c) for c in prev.club_code]
                same = r.club_code in set(prev.club_code)
                st.markdown(f"{' → '.join(clubs)}  \n"
                            + ("✅ same club" if same else f"🆕 joined {names.get(r.club_code, r.team)} this summer"))
                t = prev.agg({"gp": "sum"}).gp
                w = prev.gp / prev.gp.sum()
                st.caption(f"{int(t)} games · {(prev.mpg * w).sum():.1f} min · {(prev.pts * w).sum():.1f} pts · "
                           f"{(prev.reb * w).sum():.1f} reb · {(prev.ast * w).sum():.1f} ast · "
                           f"PIR {(prev.pir * w).sum():.1f}")
            else:
                lt = val("last_team")
                st.markdown("🆕 Not in the EuroLeague last season" + (f"  \nLast team: **{lt}**" if lt else ""))
    if len(career) > 1 or (len(career) == 1 and career.season.iloc[0] != season):
        st.markdown("**EuroLeague seasons in the data**")
        cr = career.assign(Season=career.season.map(lambda x: f"{x}-{str(x + 1)[-2:]}"),
                           Team=career.club_code.map(lambda c: names.get(c, c)))
        st.dataframe(cr[["Season", "Team", "gp", "mpg", "pts", "reb", "ast", "pir", "fp"]].sort_values(
            "Season", ascending=False), hide_index=True, width="stretch", column_config={
            "gp": "GP", "mpg": st.column_config.NumberColumn("MIN", format="%.1f"),
            "pts": st.column_config.NumberColumn("PTS", format="%.1f"),
            "reb": st.column_config.NumberColumn("REB", format="%.1f"),
            "ast": st.column_config.NumberColumn("AST", format="%.1f"),
            "pir": st.column_config.NumberColumn("PIR", format="%.1f"),
            "fp": st.column_config.NumberColumn("FP", format="%.1f", help="Fantasy points per game with the win bonus")})


# ---------------------------------------------------------------- player profile
with tab_profile:
    names = df.sort_values("pir", ascending=False)
    pid = st.selectbox("Player", names.player_id, key="pl_pid", help="Type to search",
                       format_func=dict(zip(names.player_id, names.player_name.map(ui.pname) + " · " +
                                            names.team.fillna(""))).get)
    r = df.set_index("player_id").loc[pid]
    br = board.set_index("player_id").loc[pid] if not board.empty and pid in set(board.player_id) else None
    teams = db.q("SELECT club_code, tag, team_name FROM dim_team")
    tag = dict(zip(teams.club_code, teams.tag))

    player_card(pid, r, f.season, teams)
    # fantasy strip
    if br is not None:
        k = st.columns(5)
        k[0].metric("Price", f"{br.price:.1f}" if br.price == br.price else "—",
                    f"{br.price_change:+.1f} last round" if br.price_change == br.price_change else None)
        k[1].metric("Expected FP next", f"{br.exp_fp_next:.1f}",
                    f"break-even {br.break_even:.1f}" if br.break_even == br.break_even else None, delta_color="off")
        k[2].metric("FP last 3", f"{br.fp_l3:.1f}", f"{br.fp_l3 - br.fp_pg:+.1f} vs season" if br.gp > 3 else None)
        nxt = f"{'vs' if br.next_venue == 'H' else '@'} {br.next_opp}" if isinstance(br.next_opp, str) else "—"
        k[3].metric("Next game", nxt, br.schedule_band if isinstance(br.schedule_band, str) else None,
                    delta_color="off")
        k[4].metric("Status", {"OUT": "⛔ Out", "Q": "🟠 Doubtful"}.get(br.inj_flag, "🟢 Available"),
                    (br.inj_note or "")[:40] if br.inj_flag in ("OUT", "Q") else None, delta_color="off")

    # core stats with context: rank among players of the same position (10+ minutes)
    same = df[(df.position == r.position) & (df.min_pg >= 10)] if r.position else df[df.min_pg >= 10]

    def rank(col):
        if r[col] != r[col] or same.empty:
            return None
        return f"#{int((same[col] > r[col]).sum()) + 1} of {len(same)} {r.position or ''}".strip()

    k = st.columns(6)
    for c, (lab, col) in zip(k, [("PIR / game", "pir_pg"), ("Points / game", "pts_pg"), ("Rebounds / game", "reb_pg"),
                                 ("Assists / game", "ast_pg"), ("Minutes / game", "min_pg"), ("Games", "gp")]):
        v = r[col]
        c.metric(lab, f"{int(v)}" if col == "gp" else f"{v:.1f}", None if col == "gp" else rank(col),
                 delta_color="off")

    g = db.player_games(f.season, f.rounds[1], pid)
    g = g[(g["round"] >= f.rounds[0]) & (g.minutes > 0)].copy()
    if not g.empty:
        g["opp"] = g.opp_code.map(tag).fillna(g.opp_code)
        c1, c2 = st.columns([3, 2])
        with c1:
            fig = go.Figure()
            fig.add_bar(x=g["round"], y=g.minutes, name="Minutes", yaxis="y2", opacity=0.35,
                        marker_color=[ui.ACCENT if w == "W" else "#dc2626" for w in g.wl],
                        customdata=np.stack([g.opp, g.wl], axis=1),
                        hovertemplate="R%{x} vs %{customdata[0]} (%{customdata[1]})<br>%{y:.1f} min<extra></extra>")
            fig.add_scatter(x=g["round"], y=g.fp, name="Fantasy points", mode="lines+markers+text",
                            line=dict(color="#111827", width=3), text=g.fp.round(0).astype(int),
                            textposition="top center", hovertemplate="R%{x}: %{y} FP<extra></extra>")
            fig.update_layout(title="Fantasy points by round · bars = minutes (green win, red loss)", height=380,
                              showlegend=False, yaxis=dict(title="FP"),
                              yaxis2=dict(title="Minutes", overlaying="y", side="right", range=[0, 42],
                                          showgrid=False))
            ui.round_axis(fig)
            fig.update_xaxes(range=[g["round"].min() - 0.6, g["round"].max() + 0.6])
            st.plotly_chart(fig, width="stretch")
        with c2:
            axes = {"pts_pg": "Points", "reb_pg": "Rebounds", "ast_pg": "Assists", "stl_pg": "Steals",
                    "blk_pg": "Blocks", "ts_pct": "TS%", "usg_pct": "Usage"}
            pr = [(same[a] <= r[a]).mean() * 100 if r[a] == r[a] and len(same) else 0 for a in axes]
            fig = go.Figure(go.Scatterpolar(r=pr + pr[:1], theta=list(axes.values()) + [list(axes.values())[0]],
                                            fill="toself", line_color=ui.ACCENT))
            fig.update_layout(title=f"Percentile among {r.position or 'all'} players (10+ min)", height=380,
                              polar=dict(radialaxis=dict(range=[0, 100], showticklabels=False)))
            st.plotly_chart(fig, width="stretch")

        # splits
        def split(x, name):
            if x.empty:
                return None

            def shot(m, a):
                return f"{int(x[m].sum())}-{int(x[a].sum())} ({x[m].sum() / x[a].sum():.0%})" if x[a].sum() else "—"
            return {"": name, "GP": len(x), "MIN": x.minutes.mean(), "PTS": x.pts.mean(), "TR": x.reb.mean(),
                    "AST": x.ast.mean(), "PIR": x.pir.mean(), "2PT": shot("fg2m", "fg2a"),
                    "3PT": shot("fg3m", "fg3a"), "FT": shot("ftm", "fta")}

        rows = [split(g, "Season")]
        if len(g) > 3:
            rows.append(split(g.tail(3), "Last 3"))
        if len(g) > 5:
            rows.append(split(g.tail(5), "Last 5"))
        rows += [split(g[g.venue == "H"], "Home"), split(g[g.venue == "A"], "Away"),
                 split(g[g.wl == "W"], "Wins"), split(g[g.wl == "L"], "Losses")]
        prev = db.player_games(f.season - 1, 99, pid)
        prev = prev[prev.minutes > 0] if not prev.empty else prev
        if len(prev):
            rows.append(split(prev, f"{f.season - 1}-{str(f.season)[-2:]} season"))
        sp = pd.DataFrame([x for x in rows if x])
        st.markdown("**Splits**")
        st.dataframe(sp.style.format({c: "{:.1f}" for c in ["MIN", "PTS", "TR", "AST", "PIR"]}), hide_index=True,
                     width="stretch")

        st.markdown("**Advanced**")
        adv = ["usg_pct", "ts_pct", "efg_pct", "ast_pct", "tov_pct", "orb_pct", "drb_pct", "stl_pct",
               "blk_pct", "off_rtg", "team_drtg_on", "ast_to", "fg3a_rate", "fta_rate"]
        ui.table(df[df.player_id == pid], adv, [], height=80)

        st.markdown("**Game log**")
        log = pd.DataFrame({
            "Round": g["round"], "Opp": g.opp, "H/A": g.venue, "Result": g.wl, "MIN": g.minutes, "PTS": g.pts,
            "2PT": g.fg2m.astype(int).astype(str) + "-" + g.fg2a.astype(int).astype(str),
            "3PT": g.fg3m.astype(int).astype(str) + "-" + g.fg3a.astype(int).astype(str),
            "FT": g.ftm.astype(int).astype(str) + "-" + g.fta.astype(int).astype(str),
            "TR": g.reb, "AST": g.ast, "STL": g.stl, "TO": g.tov, "BLK": g.blk, "PF": g.pf, "FD": g.fd,
            "PIR": g.pir, "FP": g.fp_win, "+/-": g.plus_minus}).sort_values("Round", ascending=False)
        st.dataframe(log.style.format({"MIN": "{:.1f}", "FP": "{:.1f}"}).map(
            lambda v: "color: #15803d; font-weight: 600" if v == "W" else
            ("color: #dc2626; font-weight: 600" if v == "L" else ""), subset=["Result"]),
            hide_index=True, width="stretch",
            column_config={"FP": st.column_config.Column("FP", help="Fantasy points with the win bonus")})

        o = fantasy.team_outlook(f.season, max(rnds), 3) if rnds else pd.DataFrame()
        o = o[o.club_code == r.club_code] if not o.empty else o
        if not o.empty:
            st.markdown("**Next games**")
            nx = pd.DataFrame({"Round": o["round"], "Opponent": o.label, "Opp DRtg": o.opp_drtg.round(1)})
            st.dataframe(nx, hide_index=True)
            st.caption("Opp DRtg = points the opponent allows per 100 possessions (higher = easier).")
