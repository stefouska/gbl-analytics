"""'Does a club / a coach do better with a specific referee?' — every (entity, referee) pair with enough games,
compared with all the OTHER games of the same entity (wins, free-throw edge, foul edge, season by season)."""
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st


def pairs(g: pd.DataFrame, rf: pd.DataFrame, entity: str) -> pd.DataFrame:
    """g: one row per entity per game (game_key, season, entity col, wl, fta, opp_fta, pf, fd);
    rf: game_key, referee. Returns one row per (entity, referee)."""
    g = g.copy()
    g["win"] = (g.wl == "W").astype(int)
    g["ft_edge"] = g.fta - g.opp_fta
    g["foul_edge"] = g.fd - g.pf                    # opponent fouls − own fouls
    x = g.merge(rf[["game_key", "referee"]].drop_duplicates(), on="game_key")
    tot = g.groupby(entity).agg(G=("game_key", "nunique"), W=("win", "sum"), FT=("ft_edge", "sum"),
                                FO=("foul_edge", "sum"))
    w = x.groupby([entity, "referee"]).agg(n=("game_key", "nunique"), w=("win", "sum"), ft=("ft_edge", "mean"),
                                           fo=("foul_edge", "mean"), ft_pos=("ft_edge", lambda s: int((s > 0).sum())),
                                           fo_pos=("foul_edge", lambda s: int((s > 0).sum()))).reset_index()
    w = w.join(tot, on=entity)
    w["o_n"] = w.G - w.n
    w["o_w"] = w.W - w.w
    w["win_with"] = w.w / w.n
    w["win_without"] = w.o_w / w.o_n.where(w.o_n > 0)
    w["wins_above"] = (w.win_with - w.win_without) * w.n          # extra wins vs the entity's usual win rate
    w["ft_without"] = (w.FT - w.ft * w.n) / w.o_n.where(w.o_n > 0)
    w["fo_without"] = (w.FO - w.fo * w.n) / w.o_n.where(w.o_n > 0)
    w["ft_above"] = w.ft - w.ft_without
    w["fo_above"] = w.fo - w.fo_without
    w["record_with"] = w.w.astype(str) + "-" + (w.n - w.w).astype(str)
    w["record_without"] = w.o_w.astype(str) + "-" + (w.o_n - w.o_w).astype(str)
    w["standout"] = (w.wins_above / 3).fillna(0) + (w.ft_above / 3).fillna(0)
    return w


def render(g: pd.DataFrame, rf: pd.DataFrame, entity: str, label: str, key: str, note: str = ""):
    """The full section: scatter, table and a spotlight card for one pair (like the 3StepsBasket thread)."""
    if g.empty or rf.empty:
        st.info("No games with known referees yet.")
        return
    p = pairs(g, rf, entity)
    mx = int(p.n.max()) if len(p) else 1
    c1, c2 = st.columns([1, 2])
    min_n = c1.slider("Min. games together", 1, max(1, mx), min(max(1, mx), 6), key=f"{key}_min")
    pick_e = c2.multiselect(label, sorted(p[entity].unique()), key=f"{key}_ent")
    q = p[p.n >= min_n].copy()
    q["win_with"] = q.win_with * 100
    q["win_without"] = q.win_without * 100
    if pick_e:
        q = q[q[entity].isin(pick_e)]
    if q.empty:
        st.info("No pairs with that many games together — lower the minimum.")
        return
    st.markdown(f"**Does a {label.lower()} do better with a specific referee?**  \n"
                f"Each dot: one referee and one {label.lower()}, at least {min_n} games together "
                f"({len(q)} in total)" + (f" · {note}" if note else ""))
    fig = px.scatter(q, x="ft_above", y="wins_above", hover_name=q[entity] + " × " + q.referee, size="n",
                     height=440, labels={"ft_above": f"Free throws per game above the {label.lower()}'s usual →",
                                         "wins_above": f"↑ Wins above what the {label.lower()} usually gets"},
                     custom_data=["record_with", "record_without", "n"])
    fig.update_traces(marker=dict(color="#9ca3af", line=dict(width=0)),
                      hovertemplate="%{hovertext}<br>%{customdata[2]} games · with: %{customdata[0]} · other games: "
                                    "%{customdata[1]}<extra></extra>")
    top = q.nlargest(1, "standout")
    fig.add_scatter(x=top.ft_above, y=top.wins_above, mode="markers+text", text=["?"], textposition="top left",
                    marker=dict(size=16, color="#ea580c", line=dict(width=2, color="white")), showlegend=False,
                    hoverinfo="skip")
    fig.add_hline(y=0, line_color="#d1d5db")
    fig.add_vline(x=0, line_color="#d1d5db")
    st.plotly_chart(fig, width="stretch")

    st.dataframe(q.sort_values("standout", ascending=False)[
        [entity, "referee", "n", "record_with", "record_without", "win_with", "win_without", "wins_above", "ft",
         "ft_without", "ft_above", "fo", "fo_without", "ft_pos", "fo_pos"]],
        hide_index=True, width="stretch", height=min(36 * (len(q) + 1) + 2, 520), column_config={
            entity: label, "referee": "Referee", "n": "Games",
            "record_with": "W-L with", "record_without": "W-L other games",
            "win_with": st.column_config.NumberColumn("Win % with", format="%.0f%%"),
            "win_without": st.column_config.NumberColumn("Win % other", format="%.0f%%"),
            "wins_above": st.column_config.NumberColumn("Wins above usual", format="%+.1f",
                                                        help="Extra wins compared with the win rate of all the other games"),
            "ft": st.column_config.NumberColumn("FT vs opp. with", format="%+.1f",
                                                help="Its free throws − the opponent's, per game, with this referee"),
            "ft_without": st.column_config.NumberColumn("FT vs opp. other", format="%+.1f"),
            "ft_above": st.column_config.NumberColumn("FT above usual", format="%+.1f"),
            "fo": st.column_config.NumberColumn("Opp. fouls − own, with", format="%+.1f"),
            "fo_without": st.column_config.NumberColumn("…other games", format="%+.1f"),
            "ft_pos": st.column_config.NumberColumn("Games more FT", help="Games with more free throws than the opponent"),
            "fo_pos": st.column_config.NumberColumn("Games opp. more fouls",
                                                    help="Games where the opponent was called for more fouls")})
    # spotlight card
    opts = list(q.sort_values("standout", ascending=False).apply(lambda r: f"{r[entity]} × {r.referee}", axis=1))
    sel = st.selectbox("Spotlight", opts, key=f"{key}_spot")
    r = q.iloc[opts.index(sel)] if sel in opts else q.iloc[0]
    st.markdown(f"#### {r[entity]} with {r.referee}")
    st.caption(f"{int(r.n)} games, compared with every other {r[entity]} game")
    k = st.columns(4)
    k[0].metric("Wins – losses", r.record_with, f"other games {r.record_without}", delta_color="off")
    k[1].metric("Win rate", f"{r.win_with:.0f}%", f"other games {r.win_without:.0f}%" if r.win_without == r.win_without
                else None, delta_color="off")
    k[2].metric("Free throws vs opponent, per game", f"{r.ft:+.1f}",
                f"other games {r.ft_without:+.1f}" if r.ft_without == r.ft_without else None, delta_color="off")
    k[3].metric("Opponent fouls vs own fouls, per game", f"{r.fo:+.1f}",
                f"other games {r.fo_without:+.1f}" if r.fo_without == r.fo_without else None, delta_color="off")
    st.markdown(f"{r[entity]} shot more free throws than the opponent in **{int(r.ft_pos)} of {int(r.n)}** games "
                f"with this referee; opponents were called for more fouls in **{int(r.fo_pos)} of {int(r.n)}**.")
    gg = g.copy()
    gg["ft_edge"] = gg.fta - gg.opp_fta
    with_ = gg[gg[entity].eq(r[entity]) & gg.game_key.isin(rf[rf.referee == r.referee].game_key)]
    oth = gg[gg[entity].eq(r[entity]) & ~gg.game_key.isin(with_.game_key)]
    by = pd.DataFrame({"games with": with_.groupby("season").size(),
                       "FT edge with": with_.groupby("season").ft_edge.mean(),
                       "FT edge other": oth.groupby("season").ft_edge.mean()}).dropna(how="all")
    if len(by) > 1:
        by["gap"] = by["FT edge with"] - by["FT edge other"]
        by.index = [f"{s}-{str(s + 1)[-2:]}" for s in by.index]
        st.markdown("**Season by season**")
        st.dataframe(by, width="stretch", column_config={
            "FT edge with": st.column_config.NumberColumn(format="%+.1f"),
            "FT edge other": st.column_config.NumberColumn(format="%+.1f"),
            "gap": st.column_config.NumberColumn("Gap", format="%+.1f")})
    st.caption("Three referees share every game and the opponent and home court matter more: a pair that stands out "
               "is a pattern worth a look, not proof of anything.")
