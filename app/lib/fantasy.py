"""Fantasy decision engine: form, risk, value, schedule, picks, coaches, prices.

Built from the logic of the Power BI 'Fantasy Picks / Elite Picks / Coaches /
Price' pages, with fixes documented in docs/METRICS.md (section 'Fantasy').
All windows (L1/L3/L5) = the player's last N games PLAYED, and are AVERAGES.
Availability (DNP) is measured separately against the team's played games.
"""
import re

import numpy as np
import pandas as pd

from .db import q

WIN_BONUS = 0.10
PRIOR_GAMES = 2          # Exp FP: after n games the player's own numbers weigh n/(n+2), his price the rest
MATCHUP_GAMES = 5        # opponent 'FP allowed' weighs g/(g+5) after g games (noisy early in the season)
RATING_GAMES = 4         # team ORtg/DRtg/pace weigh g/(g+4), the league average the rest
MIN_PRICE = 4.0          # the game's floor price
INJ_MAX_MIN = 12         # most extra minutes a player gets from injured teammates
INJ_EFFICIENCY = 0.85    # value of those extra minutes vs his usual FP per minute


def _pct(s: pd.Series, ascending=True) -> pd.Series:
    """0-100 percentile score (NaN stays NaN)."""
    return s.rank(pct=True, ascending=ascending) * 100


def team_drtg(season: int, max_round: int) -> pd.DataFrame:
    """Defensive rating per team (points allowed per 100 possessions), shrunk toward the league
    average early in the season exactly like the game predictions (see team_ratings)."""
    r = team_ratings(season, max_round)
    return r[["drtg"]].reset_index() if not r.empty else pd.DataFrame(columns=["club_code", "drtg"])


def upcoming_games(season: int, as_of_round: int, n: int = 3) -> pd.DataFrame:
    """The next n games of every team: the games after the last round the team has PLAYED (data up to
    `as_of_round`). Mid-round this matters: a Turn 2 team still has its game of the current round ahead,
    a Turn 1 team has already played it. k = 1 for the next game, 2 for the one after..."""
    g = q("""
        WITH last AS (SELECT club_code, max(round) AS last_round FROM fct_team_game
                      WHERE season = ? AND round <= ? GROUP BY 1)
        SELECT s.club_code, s.round, s.turn, s.game_date, s.start_time, s.opp_code, coalesce(o.tag, s.opp_code) AS opp,
               s.venue, row_number() OVER (PARTITION BY s.club_code ORDER BY s.round, s.game_date) AS k
        FROM fct_team_schedule s
        LEFT JOIN last l USING (club_code)
        LEFT JOIN dim_team o ON o.club_code = s.opp_code
        WHERE s.season = ? AND s.round > coalesce(l.last_round, 0)
        QUALIFY k <= ?
        ORDER BY s.club_code, k""", [season, as_of_round, season, n])
    return g


def next_fixtures(season: int, as_of_round: int) -> pd.DataFrame:
    """Next scheduled game of every team (empty if no schedule)."""
    g = upcoming_games(season, as_of_round, 1)
    return g.rename(columns={"opp_code": "next_opp_code", "opp": "next_opp", "venue": "next_venue",
                             "round": "next_round", "turn": "next_turn", "game_date": "next_date"})[
        ["club_code", "next_opp_code", "next_opp", "next_venue", "next_round", "next_turn", "next_date"]]


def round_status(season: int) -> dict:
    """current = the round being played now or about to start (first round with an unplayed game);
    next = the one after. For each: the turns (days) and whether they are played."""
    s = q("""SELECT round, turn, min(game_date) AS game_date, bool_and(played) AS played, count(*) / 2 AS games
             FROM fct_team_schedule WHERE season = ? GROUP BY 1, 2 ORDER BY 1, 2""", [season])
    if s.empty:
        return {}
    open_rounds = s[~s.played]["round"].unique()
    if not len(open_rounds):
        return {"current": None, "next": None, "turns": s}
    cur = int(open_rounds[0])
    nxt = int(open_rounds[1]) if len(open_rounds) > 1 else None
    return {"current": cur, "next": nxt, "in_progress": bool(s[(s["round"] == cur)].played.any()), "turns": s}


def latest_prices(season: int) -> pd.DataFrame:
    p = q("SELECT * FROM fct_prices WHERE season = ? AND player_id IS NOT NULL", [season])
    if p.empty:
        return pd.DataFrame(columns=["player_id", "price", "prev_price", "price_round"])
    last = p["round"].max()
    cur = p[p["round"] == last][["player_id", "price"]]
    prev = p[p["round"] == last - 1][["player_id", "price"]].rename(columns={"price": "prev_price"})
    out = cur.merge(prev, on="player_id", how="left")
    out["price_round"] = last
    return out.drop_duplicates("player_id")


def injuries() -> pd.DataFrame:
    return q("""SELECT DISTINCT ON (player_id) player_id, availability AS inj_flag, status AS inj_status,
                      comments AS inj_note, injury_level FROM fct_injuries WHERE player_id IS NOT NULL
                ORDER BY player_id, injury_level DESC""")


def defense_vs_position(season: int, as_of_round: int) -> pd.DataFrame:
    """What each team ALLOWS to opposing players, overall and by position (G/F/C).
    Replaces AvgPtsAllowed / _G / _F / _C, AvgRawPtsAllowed, Total/AvgPPointsAllowed_PerPlayer."""
    df = q("""
        SELECT p.opp_code AS club_code, coalesce(d.position, '?') AS position,
               count(*) AS player_games, sum(p.pts) AS pts_allowed, avg(p.pts) AS pts_per_player,
               avg(p.fp) AS fp_per_player, avg(p.reb) AS reb_per_player, avg(p.ast) AS ast_per_player
        FROM fct_player_game p LEFT JOIN dim_player d USING (player_id)
        WHERE p.season = ? AND p.round <= ? AND p.played = 1
        GROUP BY GROUPING SETS ((p.opp_code, coalesce(d.position, '?')), (p.opp_code))""", [season, as_of_round])
    df["position"] = df.position.fillna("All")
    return df


def _usage(g: pd.DataFrame) -> pd.Series:
    num = (g.fga + 0.44 * g.fta + g.tov) * (g.tm_minutes / 5)
    den = g.minutes * (g.tm_fga + 0.44 * g.tm_fta + g.tm_tov)
    return (num / den.replace(0, np.nan)).where(g.minutes > 0)


def player_board(games: pd.DataFrame, season: int, as_of_round: int) -> pd.DataFrame:
    """One row per player with every fantasy metric. `games` = db.player_games(season, as_of_round)."""
    g = games[games["round"] <= as_of_round].copy()
    if g.empty:
        return pd.DataFrame()
    g["usg"] = _usage(g)
    played = g[g.minutes > 0].sort_values(["player_id", "round"])

    # team games played so far (for DNP / availability)
    team_rounds = g.groupby("club_code")["round"].apply(lambda s: sorted(s.unique())).to_dict()

    rows = []
    for pid, pg in played.groupby("player_id"):
        last = pg.iloc[-1]
        n = len(pg)
        l1, l3, l5 = pg.tail(1), pg.tail(3), pg.tail(5)
        fp_season = pg.fp.mean()
        min_total, min_l3 = pg.minutes.sum(), l3.minutes.sum()

        # --- form index: per-minute FP of last 3 vs season, shrunk towards season on small samples
        fpm_season = pg.fp.sum() / min_total if min_total else np.nan
        fpm_l3 = l3.fp.sum() / min_l3 if min_l3 else np.nan
        w = min(1.0, min_l3 / 60)
        form = (w * fpm_l3 + (1 - w) * fpm_season) / fpm_season if fpm_season and fpm_season > 0 else np.nan
        if min_total < 30 or min_l3 < 20:
            form = np.nan

        # --- stability & volatility
        cv_min = pg.minutes.std(ddof=0) / pg.minutes.mean() if pg.minutes.mean() else np.nan
        role = (pg.minutes >= 20).mean() if n >= 6 else np.nan
        cv_fp = pg.fp.std(ddof=0) / fp_season if fp_season and fp_season > 0 else np.nan
        perf_stab = 1 / (1 + cv_fp) if (n >= 6 and min_total >= 80 and fp_season >= 5) else np.nan

        # --- availability (vs the team's games; a player traded mid-season uses his current team)
        tr = team_rounds.get(last.club_code, [])
        dnp_season = max(0, len(tr) - n)
        last3_team = tr[-3:]
        dnp_l3 = sum(1 for r in last3_team if r not in set(pg["round"]))
        availability = n / len(tr) if tr else np.nan

        eff = (pg.pts + pg.reb + pg.ast + pg.stl + pg.blk - (pg.fga - pg.fgm) - (pg.fta - pg.ftm) - pg.tov)
        wins, losses = pg[pg.wl == "W"].fp, pg[pg.wl == "L"].fp
        min_l5 = l5.minutes.mean()

        usg_season = np.average(pg.usg.fillna(0), weights=pg.minutes) if min_total else np.nan
        usg_l3 = np.average(l3.usg.fillna(0), weights=l3.minutes) if min_l3 else np.nan

        rows.append({
            "player_id": pid, "player_name": last.player_name, "team": last.team, "club_code": last.club_code,
            "position": last.position, "gp": n, "min_pg": pg.minutes.mean(), "min_total": min_total,
            "fp_pg": fp_season, "fp_l1": l1.fp.iloc[0], "fp_l1_win": l1.fp_win.iloc[0],
            "fp_l1_round": int(l1["round"].iloc[0]), "fp_l3": l3.fp.mean(), "fp_l5": l5.fp.mean(),
            "fp_max_l5": l5.fp.max(), "fp_sd": pg.fp_win.std() if len(pg) >= 3 else np.nan,
            "pts_l3": l3.pts.mean(), "pir_l3": l3.pir.mean(), "fp_total": pg.fp.sum(),
            "fp_win_pg": pg.fp_win.mean(), "min_l5": min_l5, "min_l3_total": min_l3,
            "expected_min": float(np.clip(min_l5, 15, 32)), "minutes_factor": float(np.clip(min_l5, 15, 32)) / 30,
            "win_impact": wins.mean() - losses.mean() if len(wins) and len(losses) else np.nan,
            "eff_trend": eff.tail(2).mean() - eff.iloc[-4:-2].mean() if n >= 4 else np.nan,
            "eff_last": eff.iloc[-1], "last_round": int(last["round"]),
            "form_index": form, "spike_l1": l1.fp.iloc[0] / fp_season if fp_season > 0 else np.nan,
            "min_l3": l3.minutes.mean(), "min_trend": l3.minutes.mean() / pg.minutes.mean() if pg.minutes.mean() else np.nan,
            "usg_season": usg_season, "usg_l3": usg_l3,
            "usg_trend": usg_l3 / usg_season if usg_season else np.nan,
            "min_volatility": cv_min, "role_stability": role, "perf_stability": perf_stab,
            "dnp_season": dnp_season, "dnp_l3": dnp_l3, "availability": availability,
        })
    b = pd.DataFrame(rows)

    # --- prices
    pr = latest_prices(season)
    b = b.merge(pr, on="player_id", how="left")
    b["price_change"] = b.price - b.prev_price
    # the game's own current price wins when we have it (Stats table of the game, read with the Dunkest token)
    try:
        gp = q("""SELECT player_id, any_value(price) AS game_price, any_value(popularity) AS pop,
                         any_value(popularity_prev) AS pop_prev, any_value(prob_playing) AS game_prob
                  FROM fct_game_players WHERE player_id IS NOT NULL GROUP BY 1""").set_index("player_id")
    except Exception:
        gp = pd.DataFrame()
    if len(gp):
        b["price"] = b.player_id.map(gp.game_price).fillna(b.price)
        b["selected_pct"] = (b.player_id.map(gp["pop"]) * 100).round(1)          # % of ALL managers
        b["selected_change"] = ((b.player_id.map(gp["pop"]) - b.player_id.map(gp.pop_prev)) * 100).round(1)
        b["game_prob"] = b.player_id.map(gp.game_prob)
    b["value_season"] = b.fp_pg / b.price
    b["value_l3"] = b.fp_l3 / b.price
    # The game's price rule (checked on round 1: 219 of 225 players within rounding):
    #   price change = (fantasy points with win bonus − 1.1 × price before the game) / 25, rounded to 0.1,
    #   never below MIN_PRICE. So 1.1 × price = the FP a player needs just to keep his price.
    b["break_even"] = 1.1 * b.price

    # --- injuries
    inj = injuries()
    b = b.merge(inj, on="player_id", how="left")
    # the game's own availability (probability of playing 0 / 50% / 100%) completes the injury report
    try:
        gpr = q("SELECT player_id, any_value(prob_playing) AS p FROM fct_game_players "
                "WHERE player_id IS NOT NULL AND prob_playing IS NOT NULL GROUP BY 1").set_index("player_id").p
    except Exception:
        gpr = pd.Series(dtype=float)
    if len(gpr):
        gp_ = b.player_id.map(gpr)
        out_ = (gp_ == 0) & (b.inj_flag.fillna("OK") != "OUT")
        q_ = (gp_ == 0.5) & b.inj_flag.fillna("OK").isin(["OK"])
        b.loc[out_, "inj_flag"] = "OUT"
        b.loc[q_, "inj_flag"] = "Q"
        if "inj_note" in b:
            b.loc[out_ | q_, "inj_note"] = b.loc[out_ | q_, "inj_note"].fillna("").astype(str).str.strip() \
                .where(lambda x: x != "", "per the fantasy game")
    b["inj_flag"] = b.inj_flag.fillna("OK")
    b["unavailable"] = b.inj_flag.isin(["OUT", "Q"])
    b["injury_level"] = b.injury_level.fillna(0).astype(int)          # 'Injury Risk 2 (All Players)'
    b["injury_label"] = b.injury_level.map({0: "🟢 Healthy", 1: "🟡 Minor issue", 2: "🟠 Questionable",
                                            3: "🟥 High risk", 4: "⛔ Out"})
    b["injury_prob"] = 1 - b.availability                             # 'Injury Probability'
    b["durability_label"] = np.select([b.injury_prob.isna(), b.injury_prob <= 0.10, b.injury_prob <= 0.30],
                                      [None, "🟢 Very durable", "🟡 Some risk"], default="🔴 Often out")
    # injured teammates at the same position free up minutes. Weight = how much he plays (MIN/G ÷ 30)
    # × status (out 1, doubtful ½) × how recent (already missed the last 2 team games → his minutes are
    # already in everyone's numbers → 0). Players out since before the season never played → not counted.
    iw = b[b.unavailable].assign(
        status=lambda x: np.where(x.inj_flag == "OUT", 1.0, 0.5),
        fresh=lambda x: 1 - (x.dnp_l3 / 2).clip(0, 1))
    iw["freed"] = iw.status * iw.fresh * iw.min_pg            # minutes per game that someone else will play
    iw = iw[iw.freed > 1].groupby(["club_code", "position"]).agg(inj_teammates=("freed", "size"),
                                                                 freed_min=("freed", "sum"))
    b = b.merge(iw.reset_index(), on=["club_code", "position"], how="left")
    b["inj_teammates"] = b.inj_teammates.fillna(0).astype(int)
    # the freed minutes go to the healthy players of the same position and team, in proportion to their minutes
    # (2 guards out and one left → he gets them all), capped at INJ_MAX_MIN per game and at 34 minutes in total.
    # Extra minutes are worth his FP per minute × INJ_EFFICIENCY (backups' extra minutes are a bit less productive).
    healthy = ~b.unavailable & (b.min_pg >= 5)
    share = b.min_pg.where(healthy) / b.min_pg.where(healthy).groupby([b.club_code, b.position]).transform("sum")
    room = (34 - b.min_l3.fillna(b.min_pg)).clip(lower=0)
    b["inj_extra_min"] = np.minimum(np.minimum(b.freed_min.fillna(0) * share.fillna(0), INJ_MAX_MIN), room).round(1)
    fp_min = (b.fp_total / b.min_total).where(b.min_total >= 20).fillna(0).clip(lower=0)
    b["inj_boost"] = (b.inj_extra_min * fp_min * INJ_EFFICIENCY).clip(upper=15).round(1)

    # --- schedule: next opponent's defensive rating (higher DRtg = weaker defense = easier)
    dr = team_drtg(season, as_of_round)
    nf = next_fixtures(season, as_of_round)
    b = b.merge(nf, on="club_code", how="left").merge(
        dr.rename(columns={"club_code": "next_opp_code", "drtg": "next_opp_drtg"}), on="next_opp_code", how="left")
    lg_drtg = dr.drtg.mean()
    cut_lo, cut_hi = dr.drtg.quantile([0.33, 0.66]) if len(dr) else (np.nan, np.nan)
    b["schedule_band"] = np.select(
        [b.next_opp_drtg.isna(), b.next_opp_drtg <= cut_lo, b.next_opp_drtg <= cut_hi],
        [None, "Tough", "Medium"], default="Easy")

    # --- potential: usage x minutes x matchup softness (fixes the old formula that
    #     divided by opponent DRtg and so rewarded TOUGH defenses)
    b["potential"] = b.usg_l3 * 100 * (b.min_l3 / 30) * (b.next_opp_drtg.fillna(lg_drtg) / lg_drtg)

    # --- defense vs position of the next opponent
    dvp = defense_vs_position(season, as_of_round)
    b = b.merge(dvp.rename(columns={"club_code": "next_opp_code", "pts_per_player": "next_opp_pts_allowed_pos",
                                    "fp_per_player": "next_opp_fp_allowed_pos"})
                [["next_opp_code", "position", "next_opp_pts_allowed_pos", "next_opp_fp_allowed_pos"]],
                on=["next_opp_code", "position"], how="left")
    opp_games = q("SELECT club_code, count(*) g FROM fct_team_game WHERE season = ? AND round <= ? GROUP BY 1",
                  [season, as_of_round]).set_index("club_code").g
    probs = next_game_probs(season, as_of_round)
    b["next_win_prob"] = b.club_code.map(probs.win_prob) if not probs.empty else np.nan

    # --- expected fantasy points in the NEXT game (includes the expected +10% win bonus)
    #   1. recent level = 50% last 3 + 30% season + 20% last 5
    #   2. early in the season blended with the price (the game prices a player ≈ at his expected FP):
    #      own numbers weigh n/(n+2) after n games → 33% after 1 game, 60% after 3, 71% after 5, 83% after 10
    #   3. × matchup: FP the opponent allows to this position vs the average, shrunk by its games played
    #   4. × home 1.03 / away 0.97 · × (1 + 10% × win probability) · × 0 out / 0.5 doubtful
    pos_avg = dvp[dvp.position != "All"].groupby("position").fp_per_player.mean()
    obs = 0.5 * b.fp_l3 + 0.3 * b.fp_pg + 0.2 * b.fp_l5
    w = b.gp / (b.gp + PRIOR_GAMES)
    b["exp_fp_base"] = np.where(b.price.notna(), w * obs + (1 - w) * b.price, obs) + b.inj_boost.fillna(0)
    proj = game_projections(b, season, as_of_round, 3)
    avail = np.select([b.inj_flag == "OUT", b.inj_flag == "Q"], [0.0, 0.5], default=1.0)
    if not proj.empty:
        p1 = proj[proj.k == 1].set_index("player_id")
        b["matchup_factor"] = b.player_id.map(p1.matchup).fillna(1.0)
        b["exp_fp_next"] = b.player_id.map(p1.exp_fp)
        b["exp_fp_3"] = b.player_id.map(proj.groupby("player_id").exp_fp.sum()).round(1)
        b["games_3"] = b.player_id.map(proj.groupby("player_id").size())
    else:
        b["matchup_factor"] = 1.0
        b["exp_fp_3"], b["games_3"] = np.nan, 0
    if "exp_fp_next" not in b:                       # no upcoming games in the schedule
        b["exp_fp_next"] = np.nan
    b["exp_fp_next"] = pd.to_numeric(b.exp_fp_next, errors="coerce")
    b["exp_fp_next"] = pd.to_numeric(b.exp_fp_next.fillna((b.exp_fp_base * avail).clip(lower=0).round(1)), errors="coerce")

    # --- market: expected price move after the next game, fair price (price at which he would stop moving)
    move = ((b.exp_fp_next - b.break_even) / 25).round(1)
    b["exp_price_move"] = np.maximum(move, MIN_PRICE - b.price).where(b.price.notna())
    b["fair_price"] = (b.exp_fp_base * (1 + WIN_BONUS / 2) / 1.1).clip(lower=MIN_PRICE).round(1)
    b["price_gap"] = (b.fair_price - b.price).round(1)

    # --- potential: usage x minutes x matchup softness (fixes the old formula that
    #     divided by opponent DRtg and so rewarded TOUGH defenses)
    b["potential"] = b.usg_l3 * 100 * (b.min_l3 / 30) * (b.next_opp_drtg.fillna(lg_drtg) / lg_drtg)

    # --- composite scores (percentiles within eligible players)
    rounds_played = max(len(v) for v in team_rounds.values()) if team_rounds else 0
    b["eligible"] = (b.gp >= min(5, max(1, rounds_played))) & (b.min_total >= min(60, 12 * max(1, rounds_played)))
    e = b.eligible
    b["performance"] = b.fp_pg * b.form_index.fillna(1.0)
    b["exp_value"] = b.exp_fp_next / b.price
    b.loc[e, "perf_score"] = _pct(b.loc[e, "exp_fp_next"])
    b.loc[e, "value_score"] = _pct(b.loc[e, "exp_value"])
    # safety: share of 20+ minute games (needs 6 games; before that minutes ÷ 30), minus minute swings and absences
    safety = b.role_stability.fillna((b.min_pg / 30).clip(0, 1)) \
        - 0.5 * b.min_volatility.fillna(0) - 0.5 * (1 - b.availability.fillna(1))
    b.loc[e, "risk_score"] = _pct(safety[e])          # higher = SAFER
    b.loc[e, "schedule_score"] = _pct(b.loc[e, "matchup_factor"])

    # PICK SCORE = 50% expected FP next + 30% expected FP per credit + 20% safety (matchup is inside Exp FP)
    b["pick_score"] = (0.5 * b.perf_score + 0.3 * b.value_score.fillna(50) + 0.2 * b.risk_score).round(0)
    # the injury opportunity is already inside Exp FP (extra minutes), so it is not added a second time
    b["pick_score_inj"] = np.where(b.unavailable, np.nan, b.pick_score)
    b["pick_tier"] = np.select(
        [b.unavailable & b.pick_score.notna(), b.pick_score_inj >= 85, b.pick_score_inj >= 70,
         b.pick_score_inj >= 55, b.pick_score_inj.notna()],
        ["⛔ Injured", "🔥 Must-pick", "🟢 Strong", "🟡 Fringe", "🔴 Avoid"], default=None)
    b["shortlisted"] = b.pick_score_inj.rank(ascending=False, method="first") <= 13

    # hot pick: recent production + form + value, all on the same 0-100 scale (only the top 30 get a label)
    b.loc[e, "hot_score"] = 0.5 * _pct(b.loc[e, "fp_l3"]) + 0.3 * _pct(b.loc[e, "form_index"].fillna(1)) \
        + 0.2 * b.loc[e, "value_score"].fillna(50)
    hr = b.hot_score.rank(ascending=False, method="min")
    b["hot_tier"] = np.select([hr <= 5, hr <= 15, hr <= 30], ["💥 Elite", "🔥 Strong", "🟡 Monitor"], default=None)

    # captain (points are doubled, price does not matter): 60% expected FP next + 25% ceiling
    # (best game of the last 5) + 15% safety
    b.loc[e, "ceiling_score"] = _pct(b.loc[e, "fp_max_l5"])
    b["captain_score"] = (0.6 * b.perf_score + 0.25 * b.ceiling_score + 0.15 * b.risk_score).round(0) \
        .where(~b.unavailable)
    b["captain_rank"] = b.captain_score.rank(ascending=False, method="min")
    b["captain_tier"] = np.select([b.captain_score >= 85, b.captain_score >= 70, b.captain_score >= 55,
                                   b.captain_score.notna()],
                                  ["👑 Elite captain", "⭐ Strong captain", "✅ Possible captain", "⚪ Avoid"], None)
    # misc restored measures
    b["value_total"] = b.fp_total / b.price                            # 'P Value Score (FP per Credit)'
    b["value_rank"] = b.value_total.rank(ascending=False, method="min")  # 'Rank_Value'
    b["top50_fp"] = b.fp_total.rank(ascending=False, method="first") <= 50   # 'TopPlayersDynamic'
    last_league_round = int(g["round"].max())
    b["eff_rank_round"] = b.eff_last.where(b.last_round == last_league_round).rank(ascending=False, method="min")
    b["usage_vs_min_delta"] = (b.usg_l3 - b.usg_season) / b.usg_season - (b.min_l3 - b.min_pg) / b.min_pg

    # labels
    b["form_label"] = np.select(
        [b.form_index.isna(), (b.form_index >= 1.35) & (b.fp_l3 >= 18), (b.form_index >= 1.15) & (b.fp_l3 >= 12),
         (b.form_index <= 0.90)], [None, "🚀 Super hot", "🔥 Hot", "🧊 Cold"], default="⚪ Normal")
    b["l1_label"] = np.select(
        [b.spike_l1.isna(), (b.spike_l1 >= 1.5) & (b.fp_l1 >= 25), (b.spike_l1 >= 1.25) & (b.fp_l1 >= 15),
         (b.spike_l1 <= 0.7)], [None, "💥 Explosion", "🔥 Big game", "🧊 Poor game"], default="⚪ Normal")
    b["role_label"] = np.select(
        [b.role_stability.isna(), b.role_stability >= .8, b.role_stability >= .6, b.role_stability >= .4],
        ["N/A (few games)", "🟢 Locked starter", "🟡 Stable rotation", "🟠 Fringe rotation"], default="🔴 Unstable role")
    b["risk_label"] = np.select(
        [b.risk_score.isna(), b.risk_score >= 80, b.risk_score >= 60, b.risk_score >= 40],
        [None, "🟢 Safe", "🟡 Medium", "🟠 Risky"], default="🔴 High risk")
    b["wildcard"] = np.select(
        [(b.price <= 11) & (b.pick_score >= 55) & (b.value_score >= 65) & (b.risk_score >= 50),
         (b.price <= 11) & (b.pick_score >= 55) & (b.value_score >= 65)],
        ["🟢 Safe wildcard", "🟠 High-risk wildcard"], default=None)
    b["affordable"] = b.price <= 12.5
    # Greek league load (Panathinaikos / Olympiacos): ⚠️ heavy minutes just before the EuroLeague game, 💤 rested
    gl = gbl_load(season)
    if not gl.empty:
        b = b.merge(gl, on="player_id", how="left")
        days = (pd.to_datetime(b.next_date) - pd.to_datetime(b.gbl_last_date)).dt.days
        near = days.between(0, GBL_WINDOW_DAYS)
        tired = near & (b.gbl_last_min >= GBL_TIRED_MIN)
        rested = near & (b.gbl_last_min < 5) & (b.min_pg >= 15)
        b["gbl_flag"] = np.select(
            [tired, rested],
            [b.gbl_last_min.round(0).astype("Int64").astype(str) + "' in GBL " + days.astype("Int64").astype(str)
             + "d before ⚠️", "💤 rested in GBL"], default=None)
        b.loc[b.gbl_flag.isna() & b.gbl_gp.notna(), "gbl_flag"] = "—"

    # bad score (missed FT/shots, fouls, turnovers, few assists/points — per 30 min, vs same position)
    bs = bad_score(games)
    if len(bs):
        b = b.drop(columns=[c for c in bs.columns if c != "player_id" and c in b], errors="ignore") \
            .merge(bs, on="player_id", how="left")

    gs = good_score(games, b)
    if len(gs):
        b = b.drop(columns=[c for c in gs.columns if c != "player_id" and c in b], errors="ignore") \
            .merge(gs, on="player_id", how="left")

    return b


# ---------------- game predictions ----------------
HOME_EDGE = 1.5          # points added to home / removed from away (≈3-point home advantage)
MARGIN_SD = 12.0         # typical spread of real margins around the prediction


LAST_SEASON_CARRY = 0.5  # how much of last season's gap to the league average a team keeps (rosters change)


def roster_changes(season: int) -> pd.DataFrame:
    """Per team: how much of last season's team is still there and how strong the roster is on paper.
    continuity   = share of last season's minutes of the club played by players who are on its roster now
    roster_value = sum of the 8 highest pre-season fantasy prices of the current roster
    arrivals / departures = players who joined / left (with last season's minutes and PIR per game)."""
    last = q("""SELECT p.player_id, p.club_code, d.player_name, sum(p.minutes) AS minutes, count(*) AS gp,
                       avg(p.pir) AS pir_pg
                FROM fct_player_game p LEFT JOIN dim_player d USING (player_id)
                WHERE p.season = ? AND p.minutes > 0 GROUP BY 1, 2, 3""", [season - 1])
    now = q("SELECT player_id, club_code AS club_now, player_name FROM raw_players WHERE season = ?", [season])
    if now.empty:        # no roster file (tests / old data): use the clubs of this season's games
        now = q("""SELECT DISTINCT ON (player_id) p.player_id, p.club_code AS club_now, d.player_name
                   FROM fct_player_game p LEFT JOIN dim_player d USING (player_id)
                   WHERE p.season = ? ORDER BY player_id, round DESC""", [season])
    pr = q("""SELECT player_id, price FROM fct_prices WHERE season = ? AND player_id IS NOT NULL
              QUALIFY row_number() OVER (PARTITION BY player_id ORDER BY round) = 1""", [season])
    clubs = sorted(set(now.club_now.dropna()))
    rows = []
    for c in clubs:
        roster = now[now.club_now == c]
        was = last[last.club_code == c]
        stay = was[was.player_id.isin(roster.player_id)]
        cont = stay.minutes.sum() / was.minutes.sum() if len(was) and was.minutes.sum() else np.nan
        arr = roster[~roster.player_id.isin(was.player_id)].merge(
            last.sort_values("minutes").drop_duplicates("player_id", keep="last")[["player_id", "club_code", "pir_pg"]],
            on="player_id", how="left")
        dep = was[~was.player_id.isin(roster.player_id)].sort_values("minutes", ascending=False)
        rv = pr[pr.player_id.isin(roster.player_id)].price.nlargest(8).sum()
        rows.append({"club_code": c, "continuity": cont, "roster_value": rv if rv > 0 else np.nan,
                     "n_arrivals": len(arr), "n_departures": len(dep),
                     "arrivals": arr, "departures": dep})
    return pd.DataFrame(rows).set_index("club_code") if rows else pd.DataFrame()


def _last_season_prior(season: int) -> pd.DataFrame:
    """Starting point of each team for `season`, as gaps to the league average (ORtg, DRtg, pace).
    It mixes two views, weighted by roster continuity (share of last season's minutes still on the roster):
      • kept players  → half of the team's gap to the average LAST season
      • new players   → roster value on paper (8 highest pre-season prices), scaled to last season's spread
    Tested on the first 18 games of 2026-27: margin error 9.2 (no prior) → 8.6 (last season only)
    → 8.3 (this mix). Teams that did not play last season rely on the roster value only."""
    r = q("""SELECT club_code, 100 * sum(pts) / sum(poss) AS ortg, 100 * sum(opp_pts) / sum(opp_poss) AS drtg,
                    (sum(poss) + sum(opp_poss)) / 2 * 200 / sum(minutes) AS pace
             FROM fct_team_game WHERE season = ? GROUP BY 1""", [season - 1]).set_index("club_code")
    rc = roster_changes(season)
    if r.empty and rc.empty:
        return pd.DataFrame(columns=["ortg_gap", "drtg_gap", "pace_gap"], dtype=float)
    clubs = sorted(set(r.index) | set(rc.index))
    out = pd.DataFrame(index=clubs, columns=["ortg_gap", "drtg_gap", "pace_gap"], dtype=float).fillna(0.0)
    if not r.empty:
        lg_r, lg_p = (r.ortg.mean() + r.drtg.mean()) / 2, r.pace.mean()
        net_sd = (r.ortg - r.drtg).std()
        last = pd.DataFrame({"o": LAST_SEASON_CARRY * (r.ortg - lg_r), "d": LAST_SEASON_CARRY * (r.drtg - lg_r),
                             "p": LAST_SEASON_CARRY * (r.pace - lg_p)}).reindex(clubs)
    else:
        net_sd, last = 5.0, pd.DataFrame(np.nan, index=clubs, columns=["o", "d", "p"])
    cont = rc.continuity.reindex(clubs) if not rc.empty else pd.Series(np.nan, index=clubs)
    cont = cont.where(last.o.notna(), 0.0).fillna(1.0 if rc.empty else 0.0).clip(0, 1)
    rv = rc.roster_value.reindex(clubs) if not rc.empty else pd.Series(np.nan, index=clubs)
    if rv.notna().sum() > 2:
        paper = LAST_SEASON_CARRY * net_sd * (rv - rv.mean()) / rv.std()     # net-rating gap on paper
        paper = paper.fillna(0.0)
    else:
        paper, cont = pd.Series(0.0, index=clubs), cont.where(last.o.isna(), 1.0)
    out["ortg_gap"] = cont * last.o.fillna(0) + (1 - cont) * paper / 2
    out["drtg_gap"] = cont * last.d.fillna(0) - (1 - cont) * paper / 2
    out["pace_gap"] = cont * last.p.fillna(0)
    return out


def team_ratings(season: int, as_of_round: int, shrink: bool = True) -> pd.DataFrame:
    """ORtg, DRtg and pace per team. With shrink=True each is blended with a starting point by
    RATING_GAMES / (games + RATING_GAMES): after 1 game a team keeps 20% of its own numbers, after 4 games 50%,
    after 12 games 75%. The starting point = league average + half of the team's gap to the average LAST season
    (so Real Madrid does not start level with a weak team); league average for new teams."""
    r = q("""
        SELECT club_code, count(*) AS games,
               sum(pts) AS pts, sum(poss) AS poss, sum(opp_pts) AS opp_pts, sum(opp_poss) AS opp_poss,
               sum(minutes) AS minutes
        FROM fct_team_game WHERE season = ? AND round <= ? GROUP BY 1""", [season, as_of_round]).set_index("club_code")
    if r.empty:
        return pd.DataFrame(columns=["ortg", "drtg", "pace", "games"])
    r["ortg"] = 100 * r.pts / r.poss
    r["drtg"] = 100 * r.opp_pts / r.opp_poss
    r["pace"] = (r.poss + r.opp_poss) / 2 * 200 / r.minutes
    if shrink:
        lg_rtg = 100 * r.pts.sum() / r.poss.sum()
        lg_pace = r.pace.mean()
        w = r.games / (r.games + RATING_GAMES)
        pr = _last_season_prior(season).reindex(r.index).fillna(0.0) if season else None
        gap = (lambda c: pr[c]) if pr is not None and len(pr.columns) else (lambda c: 0.0)
        r["ortg"] = w * r.ortg + (1 - w) * (lg_rtg + gap("ortg_gap"))
        r["drtg"] = w * r.drtg + (1 - w) * (lg_rtg + gap("drtg_gap"))
        r["pace"] = w * r.pace + (1 - w) * (lg_pace + gap("pace_gap"))
    return r[["ortg", "drtg", "pace", "games"]]


_MKT = {"key": None, "lines": {}}


def market_lines() -> dict:
    """(home club, away club) -> betting-market line of the next game between them (fct_odds), cached until the
    warehouse changes. Empty when there is no odds data."""
    from . import db as _db
    try:
        key = _db.db_path().stat().st_mtime
    except OSError:
        key = None
    if _MKT["key"] != key:
        try:
            o = q("SELECT * FROM fct_odds")
            _MKT["lines"] = {(r.home_code, r.away_code): r._asdict() for r in o.itertuples(index=False)}
        except Exception:
            _MKT["lines"] = {}
        _MKT["key"] = key
    return _MKT["lines"]


def predict_game(r: pd.DataFrame, home: str, away: str, use_market: bool = True) -> dict:
    """Predicted score, margin (home − away) and home win probability from ORtg/DRtg/pace — replaced by the
    betting market (median of the bookmakers) when we have its line for this game and use_market is on."""
    if use_market:
        m = market_lines().get((home, away))
        if m is not None:
            base = predict_game(r, home, away, use_market=False)
            mg = m["home_margin"] if m["home_margin"] == m["home_margin"] else base["margin"]
            hw = m["home_prob"] if m["home_prob"] == m["home_prob"] else (
                _norm_cdf(mg / MARGIN_SD) if mg == mg else base["home_win"])
            hp = m["home_pts"] if m["home_pts"] == m["home_pts"] else base["home_pts"]
            ap = m["away_pts"] if m["away_pts"] == m["away_pts"] else base["away_pts"]
            return {"home_pts": hp, "away_pts": ap, "margin": mg, "home_win": hw, "source": "market"}
    if home not in r.index or away not in r.index:
        return {"home_pts": np.nan, "away_pts": np.nan, "margin": np.nan, "home_win": np.nan}
    pace = (r.at[home, "pace"] + r.at[away, "pace"]) / 2
    hp = (r.at[home, "ortg"] + r.at[away, "drtg"]) / 2 * pace / 100 + HOME_EDGE
    ap = (r.at[away, "ortg"] + r.at[home, "drtg"]) / 2 * pace / 100 - HOME_EDGE
    m = hp - ap
    return {"home_pts": hp, "away_pts": ap, "margin": m, "home_win": _norm_cdf(m / MARGIN_SD)}


def next_game_probs(season: int, as_of_round: int) -> pd.DataFrame:
    """For every team: next opponent, predicted margin (own − opponent) and win probability."""
    nf = next_fixtures(season, as_of_round)
    if nf.empty:
        return pd.DataFrame(columns=["pred_margin", "win_prob"])
    r = team_ratings(season, as_of_round)
    rows = []
    for row in nf.itertuples():
        home, away = (row.club_code, row.next_opp_code) if row.next_venue == "H" else (row.next_opp_code, row.club_code)
        p = predict_game(r, home, away)
        home_side = row.next_venue == "H"
        rows.append({"club_code": row.club_code, "pred_margin": p["margin"] if home_side else -p["margin"],
                     "win_prob": p["home_win"] if home_side else 1 - p["home_win"]})
    return pd.DataFrame(rows).set_index("club_code")


def game_projections(b: pd.DataFrame, season: int, as_of_round: int, n: int = 3) -> pd.DataFrame:
    """Expected fantasy points of every player in each of his team's next n games (one row per player-game).
    Same recipe as Exp FP next: level (exp_fp_base) × matchup × home/away × (1 + 10% × win chance) × availability.
    Availability: out = 0 in every game (we do not know when he returns), doubtful = ½ in the next game only."""
    up = upcoming_games(season, as_of_round, n)
    if up.empty or b.empty:
        return pd.DataFrame()
    r = team_ratings(season, as_of_round)
    wp, mf = {}, {}
    for row in up.itertuples():
        home, away = (row.club_code, row.opp_code) if row.venue == "H" else (row.opp_code, row.club_code)
        pg = predict_game(r, home, away)
        p = pg["home_win"]
        wp[(row.club_code, row.k)] = p if row.venue == "H" else 1 - p
        # betting market: the team's expected points vs our model's → a small scoring-environment factor
        f = 1.0
        if pg.get("source") == "market":
            base = predict_game(r, home, away, use_market=False)
            own_m, own_b = (pg["home_pts"], base["home_pts"]) if row.venue == "H" else (pg["away_pts"], base["away_pts"])
            if own_m == own_m and own_b == own_b and own_b > 0:
                f = float(np.clip(own_m / own_b, 0.92, 1.08))
        mf[(row.club_code, row.k)] = f
    up["win_prob"] = [wp[(c, k)] for c, k in zip(up.club_code, up.k)]
    up["market_factor"] = [mf[(c, k)] for c, k in zip(up.club_code, up.k)]
    dvp = defense_vs_position(season, as_of_round)
    pos_avg = dvp[dvp.position != "All"].groupby("position").fp_per_player.mean()
    allowed = dvp.set_index(["club_code", "position"]).fp_per_player
    # last season's FP allowed per position (full season), half of its gap to the average, as the starting point
    last = defense_vs_position(season - 1, 99)
    last = last[last.position.isin(["G", "F", "C"])]
    if not last.empty:
        la = last.groupby("position").fp_per_player.mean()
        prior_ratio = (1 + LAST_SEASON_CARRY * (last.fp_per_player / last.position.map(la) - 1))
        prior_ratio.index = pd.MultiIndex.from_arrays([last.club_code, last.position])
    else:
        prior_ratio = pd.Series(dtype=float)
    opp_games = q("SELECT club_code, count(*) g FROM fct_team_game WHERE season = ? AND round <= ? GROUP BY 1",
                  [season, as_of_round]).set_index("club_code").g
    x = b[["player_id", "club_code", "position", "exp_fp_base", "inj_flag", "price"]].merge(up, on="club_code")
    raw = pd.Series([allowed.get((o, p), np.nan) for o, p in zip(x.opp_code, x.position)], index=x.index) \
        / x.position.map(pos_avg)
    g_opp = x.opp_code.map(opp_games).fillna(0)
    prior = pd.Series([prior_ratio.get((o, p), 1.0) for o, p in zip(x.opp_code, x.position)], index=x.index)
    w = g_opp / (g_opp + MATCHUP_GAMES)
    x["matchup"] = (prior + (raw.fillna(prior) - prior) * w).clip(0.85, 1.15).fillna(1.0)
    venue = x.venue.map({"H": 1.03, "A": 0.97}).fillna(1.0)
    avail = np.select([x.inj_flag == "OUT", (x.inj_flag == "Q") & (x.k == 1)], [0.0, 0.5], default=1.0)
    x["exp_fp"] = (x.exp_fp_base * x.matchup * venue * (1 + WIN_BONUS * x.win_prob.fillna(0.5)) * avail
                   * x.market_factor.fillna(1.0)) \
        .clip(lower=0).round(1)
    return x


GBL_TIRED_MIN = 25      # minutes in a GBL game that count as a heavy load
GBL_WINDOW_DAYS = 3     # ... when it was this many days (or fewer) before the next EuroLeague game


def gbl_load(season: int) -> pd.DataFrame:
    """Greek league load of the Panathinaikos / Olympiacos players (one row per player):
    GBL games played, minutes per game, minutes in the last 7 days, last GBL game (date, minutes)."""
    try:
        g = q("SELECT * FROM fct_gbl_load WHERE season = ? AND player_id IS NOT NULL", [season])
    except Exception:                                    # warehouse built before this feature
        return pd.DataFrame()
    if g.empty:
        return g
    g["game_date"] = pd.to_datetime(g.game_date)
    today = pd.Timestamp.today().normalize()
    g = g.sort_values("game_date")
    played = g[g.minutes > 0]
    out = pd.DataFrame({
        "gbl_gp": played.groupby("player_id").size(),
        "gbl_min_pg": played.groupby("player_id").minutes.mean().round(1),
        "gbl_min_7d": g[g.game_date >= today - pd.Timedelta(days=7)].groupby("player_id").minutes.sum()})
    last = g.groupby("player_id").tail(1).set_index("player_id")
    out["gbl_last_date"] = last.game_date
    out["gbl_last_min"] = last.minutes
    out["gbl_last_opp"] = last.opp
    out["gbl_min_7d"] = out.gbl_min_7d.fillna(0)
    return out.reset_index().rename(columns={"index": "player_id"})


def coach_prices() -> pd.DataFrame:
    """club_code, tag, coach, price. Start-of-season prices from data/reference/coaches.csv (Basketball Sphere,
    20/09/2026); replaced by the latest price the game shows in the line-ups of our league (Dunkest), matched
    by the coach's surname."""
    import unicodedata
    from pathlib import Path
    f = Path(__file__).resolve().parents[2] / "data" / "reference" / "coaches.csv"
    if not f.exists():
        return pd.DataFrame(columns=["club_code", "tag", "coach", "price", "price_source"])
    c = pd.read_csv(f)
    c["price"], c["price_source"] = c.price_start.astype(float), "start of season"
    try:
        hc = q("""SELECT last_name, arg_max(price, round) AS price, max(round) AS rnd FROM fct_fantasy_rosters
                  WHERE position = 'HC' AND price IS NOT NULL GROUP BY 1""")
    except Exception:
        hc = pd.DataFrame()
    if len(hc):
        norm = lambda x: "".join(ch for ch in unicodedata.normalize("NFKD", str(x).upper())  # noqa: E731
                                 if not unicodedata.combining(ch))
        live = dict(zip(hc.last_name.map(norm), zip(hc.price, hc.rnd)))
        for i, r in c.iterrows():
            if norm(r.surname) in live:
                c.at[i, "price"] = float(live[norm(r.surname)][0])
                c.at[i, "price_source"] = f"game, round {int(live[norm(r.surname)][1])}"
    # best source: the game's own player table (current price of every coach)
    try:
        gp = q("SELECT name, club_code, price, fetched_at FROM fct_game_players WHERE position = 'HC' AND price IS NOT NULL")
    except Exception:
        gp = pd.DataFrame()
    if len(gp):
        norm2 = lambda x: "".join(ch for ch in unicodedata.normalize("NFKD", str(x).upper())  # noqa: E731
                                  if not unicodedata.combining(ch))
        gp["sur"] = gp.name.map(lambda n: norm2(re.sub(r"^[A-Za-z]+\.\s*", "", str(n))))
        when = pd.Timestamp(gp.fetched_at.max()).strftime("%d/%m") if gp.fetched_at.notna().any() else ""
        for i, r in c.iterrows():
            hit = gp[(gp.club_code == r.club_code) | gp.sur.str.contains(norm2(r.surname), regex=False)]
            if len(hit):
                c.at[i, "price"] = float(hit.price.iloc[0])
                c.at[i, "price_source"] = f"game {when}".strip()
    return c[["club_code", "tag", "coach", "price", "price_source"]]


def coach_round_points(season: int, as_of_round: int, rounds: list) -> pd.DataFrame:
    """Expected coach points of every team in each of `rounds` (club_code, round, opp, exp_pts)."""
    fx = fixtures(season, as_of_round)
    fx = fx[fx["round"].isin(rounds)]
    if fx.empty:
        return pd.DataFrame(columns=["club_code", "round", "opp", "exp_pts"])
    r = team_ratings(season, as_of_round)
    rows = []
    for g in fx.itertuples():
        p = predict_game(r, g.home_code, g.away_code)
        if p["margin"] != p["margin"]:
            continue
        rows.append({"club_code": g.home_code, "round": g.round, "opp": g.away,
                     "exp_pts": expected_coach_points(p["margin"])})
        rows.append({"club_code": g.away_code, "round": g.round, "opp": g.home,
                     "exp_pts": expected_coach_points(-p["margin"])})
    return pd.DataFrame(rows)


def league_trending(season: int) -> pd.DataFrame:
    """Transfers inside our fantasy league between the last two rounds with line-ups:
    per player, how many teams bought / sold him (from the Dunkest line-ups)."""
    try:
        ro = q("SELECT fantasy_team_id, round, player_id FROM fct_fantasy_rosters "
               "WHERE season = ? AND player_id IS NOT NULL", [season])
    except Exception:
        return pd.DataFrame()
    if ro.empty:
        return ro
    full = ro.groupby("round").fantasy_team_id.nunique()
    done = sorted(full[full > 1].index)
    if len(done) < 2:
        return pd.DataFrame()
    a, b = done[-2], done[-1]
    teams_both = set(ro[ro["round"] == a].fantasy_team_id) & set(ro[ro["round"] == b].fantasy_team_id)
    x = ro[ro.fantasy_team_id.isin(teams_both)]
    before = x[x["round"] == a].groupby("fantasy_team_id").player_id.apply(set)
    after = x[x["round"] == b].groupby("fantasy_team_id").player_id.apply(set)
    bought, sold = {}, {}
    for t in teams_both:
        for p in after.get(t, set()) - before.get(t, set()):
            bought[p] = bought.get(p, 0) + 1
        for p in before.get(t, set()) - after.get(t, set()):
            sold[p] = sold.get(p, 0) + 1
    ids = set(bought) | set(sold)
    out = pd.DataFrame({"player_id": list(ids)})
    out["league_bought"] = out.player_id.map(bought).fillna(0).astype(int)
    out["league_sold"] = out.player_id.map(sold).fillna(0).astype(int)
    out["league_net"] = out.league_bought - out.league_sold
    out["league_round"] = b
    own = x[x["round"] == b].groupby("player_id").fantasy_team_id.nunique()
    out["league_owned"] = out.player_id.map(own).fillna(0).astype(int)
    out.attrs["teams"] = len(teams_both)
    return out


# ---------------------------------------------------------------- bad score
BAD_WEIGHTS = {"miss_ft": 0.15, "miss_fg": 0.15, "pf": 0.15, "tov": 0.20, "low_ast": 0.15, "low_pts": 0.20}
BAD_RECENT = 0.6        # weight of the last 5 games vs the season


def bad_score(games: pd.DataFrame, min_minutes: float = 10.0) -> pd.DataFrame:
    """'Bad score' 0-100 per player (higher = worse): missed free throws, missed field goals, fouls, turnovers,
    few assists and few points, all per 30 minutes and ranked against players of the SAME position.
    60% the last 5 games, 40% the season. Only players averaging min_minutes or more get a score."""
    g = games[games.played & (games.minutes > 0)].copy()
    if g.empty:
        return pd.DataFrame(columns=["player_id", "bad_score"])
    g = g.sort_values(["round", "gamecode"])

    def per30(x: pd.DataFrame) -> pd.DataFrame:
        a = x.groupby("player_id").agg(min=("minutes", "sum"), gp=("minutes", "size"), ftm=("ftm", "sum"),
                                       fta=("fta", "sum"), fgm=("fgm", "sum"), fga=("fga", "sum"), pf=("pf", "sum"),
                                       tov=("tov", "sum"), ast=("ast", "sum"), pts=("pts", "sum"))
        k = 30 / a["min"].where(a["min"] > 0)
        return pd.DataFrame({"min_pg": a["min"] / a.gp, "miss_ft": (a.fta - a.ftm) * k, "miss_fg": (a.fga - a.fgm) * k,
                             "pf": a.pf * k, "tov": a.tov * k, "ast": a.ast * k, "pts": a.pts * k})

    season, recent = per30(g), per30(g.groupby("player_id").tail(5))
    pos = g.drop_duplicates("player_id", keep="last").set_index("player_id").position
    out = []
    for name, d in (("season", season), ("recent", recent)):
        d = d.join(pos)
        d = d[d.min_pg >= min_minutes]
        r = pd.DataFrame(index=d.index)
        grp = d.groupby("position")
        for c in ("miss_ft", "miss_fg", "pf", "tov"):
            r[c] = grp[c].rank(pct=True)                       # more = worse
        r["low_ast"] = 1 - grp["ast"].rank(pct=True)          # fewer = worse
        r["low_pts"] = 1 - grp["pts"].rank(pct=True)
        r[name] = sum(r[c] * w for c, w in BAD_WEIGHTS.items()) * 100
        out.append((name, d, r))
    (_, ds, rs), (_, dr, rr) = out
    b = pd.DataFrame(index=rs.index.union(rr.index))
    b["bad_season"] = rs["season"]
    b["bad_recent"] = rr["recent"]
    b["bad_score"] = (BAD_RECENT * b.bad_recent.fillna(b.bad_season) +
                      (1 - BAD_RECENT) * b.bad_season.fillna(b.bad_recent)).round(0)
    for c in ("miss_ft", "miss_fg", "pf", "tov", "ast", "pts"):          # recent per-30 numbers, for display
        b[f"bad_{c}"] = dr[c].reindex(b.index).fillna(ds[c].reindex(b.index)).round(1)
    worst = rr[list(BAD_WEIGHTS)].reindex(b.index).fillna(rs[list(BAD_WEIGHTS)].reindex(b.index))
    names = {"miss_ft": "missed FT", "miss_fg": "missed shots", "pf": "fouls", "tov": "turnovers",
             "low_ast": "few assists", "low_pts": "few points"}
    b["bad_reasons"] = worst.apply(lambda r: ", ".join(names[c] for c in r.sort_values(ascending=False).index[:2]
                                                         if r[c] >= 0.7), axis=1)
    b["bad_label"] = pd.cut(b.bad_score, [-1, 59.9, 69.9, 101], labels=["🟢 OK", "🟠 Watch", "🔴 Bad form"]).astype(str)
    b.loc[b.bad_score.isna(), "bad_label"] = None
    return b.reset_index().rename(columns={"index": "player_id"})


# ---------------------------------------------------------------- good score
GOOD_WEIGHTS = {"fp": 0.30, "over_be": 0.20, "jump": 0.15, "pts": 0.10, "ast_reb": 0.10, "ts": 0.10, "min": 0.05}


def good_score(games: pd.DataFrame, board: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    """'Good score' 0-100 (higher = better) for the last n games: fantasy points, points above his break-even
    (1.1 × price), jump vs his season, points / assists+rebounds per 30 min, true shooting and minutes —
    each ranked against players of the same position. Needs 2+ of his last n team games and 15+ minutes."""
    g = games[games.played & (games.minutes > 0)].sort_values(["round", "gamecode"])
    if g.empty or board.empty:
        return pd.DataFrame(columns=["player_id", "good_score"])
    last = g.groupby("player_id").tail(n)
    a = last.groupby("player_id").agg(gp_l=("minutes", "size"), min_l=("minutes", "mean"), fp_l=("fp", "mean"),
                                      pts=("pts", "sum"), ast=("ast", "sum"), reb=("reb", "sum"),
                                      fga=("fga", "sum"), fta=("fta", "sum"), mins=("minutes", "sum"))
    a = a[(a.gp_l >= 2) & (a.min_l >= 15)]
    bb = board.drop_duplicates("player_id").set_index("player_id")
    a = a.join(bb[["position", "price", "fp_pg", "min_pg"]], how="inner")
    k = 30 / a.mins
    a["good_pts30"] = a.pts * k
    a["good_astreb30"] = (a.ast + a.reb) * k
    a["good_ts"] = a.pts / (2 * (a.fga + 0.44 * a.fta)).where(a.fga + a.fta > 0) * 100
    a["good_over_be"] = a.fp_l - 1.1 * a.price
    a["good_jump"] = a.fp_l - a.fp_pg
    a["good_min_jump"] = a.min_l - a.min_pg
    grp = a.groupby("position")
    r = pd.DataFrame(index=a.index)
    for key, col in (("fp", "fp_l"), ("over_be", "good_over_be"), ("jump", "good_jump"), ("pts", "good_pts30"),
                     ("ast_reb", "good_astreb30"), ("ts", "good_ts"), ("min", "good_min_jump")):
        r[key] = grp[col].rank(pct=True).fillna(0.5)
    a["good_score"] = (sum(r[c] * w for c, w in GOOD_WEIGHTS.items()) * 100).round(0)
    names = {"fp": "big FP", "over_be": "beats his price", "jump": "up on his season", "pts": "scoring",
             "ast_reb": "boards & assists", "ts": "efficient", "min": "more minutes"}
    a["good_reasons"] = r.apply(lambda x: ", ".join(names[c] for c in x.sort_values(ascending=False).index[:2]
                                                    if x[c] >= 0.75), axis=1)
    a["good_label"] = pd.cut(a.good_score, [-1, 64.9, 79.9, 101], labels=["—", "🟢 Good", "🔥 Hot"]).astype(str)
    a["good_fp"] = a.fp_l.round(1)
    a["good_games"] = a.gp_l
    keep = ["good_score", "good_label", "good_reasons", "good_fp", "good_games", "good_over_be", "good_jump",
            "good_pts30", "good_astreb30", "good_ts", "good_min_jump"]
    return a[keep].round(1).reset_index().rename(columns={"index": "player_id"})


def _norm_cdf(x):
    from math import erf, sqrt
    return 0.5 * (1 + erf(x / sqrt(2)))


# ---------------- coaches ----------------
# Official EuroLeague Fantasy Challenge coach scoring (winning margin of the real team):
#   win 1-10 or OT +10 · win 11-20 +20 · win 20+ +25 · loss 1-10 or OT -5 · loss 11-20 -10 · loss 20+ -20
COACH_BANDS = [(-np.inf, -20.5, -20), (-20.5, -10.5, -10), (-10.5, 0, -5),
               (0, 10.5, 10), (10.5, 20.5, 20), (20.5, np.inf, 25)]


def coach_points(margin, overtime=False):
    if overtime:
        return 10 if margin > 0 else -5
    a = abs(margin)
    if margin > 0:
        return 10 if a <= 10 else 20 if a <= 20 else 25
    return -5 if a <= 10 else -10 if a <= 20 else -20


def expected_coach_points(margin: float, sd: float = MARGIN_SD) -> float:
    """Σ P(margin in band) × band points, with the real margin ~ Normal(predicted, sd)."""
    if margin != margin:
        return np.nan
    return sum(pts * (_norm_cdf((hi - margin) / sd) - _norm_cdf((lo - margin) / sd))
               for lo, hi, pts in COACH_BANDS)


def coach_board(season: int, as_of_round: int) -> pd.DataFrame:
    tg = q("""SELECT t.*, dt.tag, dt.team_name FROM fct_team_game t LEFT JOIN dim_team dt USING (club_code)
              WHERE t.season = ? AND t.round <= ?""", [season, as_of_round])
    if tg.empty:
        return tg
    tg["coach_pts"] = [coach_points(m, mins > 200) for m, mins in zip(tg.margin, tg.minutes)]
    out = tg.groupby(["club_code", "tag", "team_name"]).agg(
        games=("game_key", "nunique"), wins=("wl", lambda s: (s == "W").sum()),
        coach_pts_total=("coach_pts", "sum"), coach_pts_avg=("coach_pts", "mean"),
        avg_margin=("margin", "mean")).reset_index()
    last = tg.sort_values("round").groupby("club_code").tail(1).set_index("club_code")
    out["last_coach_pts"] = out.club_code.map(last.coach_pts)
    out["losses"] = out.games - out.wins
    out["win_pct"] = out.wins / out.games

    nf = next_fixtures(season, as_of_round)
    out = out.merge(nf, on="club_code", how="left")
    probs = next_game_probs(season, as_of_round)
    out["pred_margin"] = out.club_code.map(probs.pred_margin) if not probs.empty else np.nan
    out["win_prob"] = out.club_code.map(probs.win_prob) if not probs.empty else np.nan
    out["exp_coach_pts"] = out.pred_margin.map(expected_coach_points)
    out["pred_rank"] = out.exp_coach_pts.rank(ascending=False, method="min")
    out["pick_label"] = np.select(
        [out.pred_rank == 1, out.pred_rank <= 3, out.pred_rank <= 6, out.exp_coach_pts < 0],
        ["🏆 Best coach", "🔥 Top 3", "✅ Good", "🔴 Avoid"], default="—")
    return out.sort_values("exp_coach_pts", ascending=False)


# ---------------- our fantasy league ----------------
def league_tables(season: int, only_clans: bool = False) -> dict:
    """only_clans=True keeps the teams that belong to a clan (our group inside the bigger Dunkest league)."""
    fl = q("SELECT * FROM fct_fantasy_league WHERE season = ? AND points IS NOT NULL", [season])
    fl["clan"] = fl.clan.fillna("Solo")
    if only_clans:
        fl = fl[fl.clan != "Solo"]
    if fl.empty:
        return {}
    fl["round_rank"] = fl.groupby("round").points.rank(ascending=False, method="min")
    fl["round_win"] = fl.round_rank == 1
    fl = fl.sort_values(["fantasy_team", "round"])
    fl["cum_points"] = fl.groupby("fantasy_team").points.cumsum()
    fl["cum_rank"] = fl.groupby("round").cum_points.rank(ascending=False, method="min")
    season_tbl = fl.groupby("fantasy_team").agg(
        total=("points", "sum"), avg=("points", "mean"), best=("points", "max"), worst=("points", "min"),
        round_wins=("round_win", "sum"), rounds=("round", "nunique")).reset_index()
    season_tbl["rank"] = season_tbl.total.rank(ascending=False, method="min").astype(int)
    season_tbl["vs_league_avg"] = season_tbl.avg - fl.points.mean()
    owners = fl.dropna(subset=["owner"]).drop_duplicates("fantasy_team", keep="last").set_index("fantasy_team").owner \
        if "owner" in fl else pd.Series(dtype=str)
    season_tbl["owner"] = season_tbl.fantasy_team.map(owners)
    season_tbl["clan"] = season_tbl.fantasy_team.map(fl.drop_duplicates("fantasy_team", keep="last")
                                                     .set_index("fantasy_team").clan)
    out = {"rounds": fl, "season": season_tbl.sort_values("rank"),
           "league_avg_round": fl.groupby("round").points.mean().rename("league_avg").reset_index(),
           "rounds_played": int(fl["round"].nunique()),
           "league_avg_points": float(fl.points.mean())}

    clans_ = fl[fl.clan != "Solo"]
    if len(clans_):
        c = clans_.groupby(["round", "clan"]).points.sum().unstack()
        if c.shape[1] == 2:
            a, b = c.columns
            rows = []
            for clan, other in ((a, b), (b, a)):
                diff = c[clan] - c[other]
                win = (diff > 0).astype(int)
                streak = win.groupby((win == 0).cumsum()).cumsum().max()
                rows.append({"clan": clan, "wins": int(win.sum()), "win_pct": win.mean(),
                             "points_for": c[clan].sum(), "points_against": c[other].sum(),
                             "diff": diff.sum(), "best_round": c[clan].max(), "worst_round": c[clan].min(),
                             "longest_streak": int(streak), "blowouts": int((diff >= 50).sum())})
            out["clans"] = pd.DataFrame(rows)
            out["clan_rounds"] = c.reset_index()
    return out


# ---------------- schedule difficulty ----------------
def fixtures(season: int, as_of_round: int, easy: float | None = None, medium: float | None = None) -> pd.DataFrame:
    """Every game with the difficulty for BOTH sides = the opponent's defensive rating
    (points allowed per 100 poss; higher = weaker defense = easier).
    Replaces Home/Away Schedule Difficulty, Home/Away Difficulty Band, Schedule Difficulty (+Band)."""
    f = q("""SELECT s.season, s.round, dense_rank() OVER (PARTITION BY s.season, s.round ORDER BY s.game_date) AS turn,
                    s.game_date, s.start_time, s.played, s.home_code, s.away_code, s.game_key,
                    th.tag AS home, ta.tag AS away, th.team_name AS home_name, ta.team_name AS away_name
             FROM stg_schedule s
             LEFT JOIN dim_team th ON th.club_code = s.home_code
             LEFT JOIN dim_team ta ON ta.club_code = s.away_code
             WHERE s.season = ? ORDER BY s.round, s.game_date""", [season])
    if f.empty:
        return f
    dr = team_drtg(season, as_of_round).set_index("club_code").drtg
    f["home_difficulty"] = f.away_code.map(dr)     # the home team faces the away team's defense
    f["away_difficulty"] = f.home_code.map(dr)
    # same bands as the player board: weakest third of defenses = Easy, strongest third = Tough
    if easy is None or medium is None:
        medium, easy = (dr.quantile([0.33, 0.66]).tolist() if len(dr) else (np.inf, np.inf))

    def band(x):
        return np.select([x.isna(), x > easy, x > medium], [None, "Easy", "Medium"], default="Tough")
    f["home_band"], f["away_band"] = band(f.home_difficulty), band(f.away_difficulty)
    return f


def team_outlook(season: int, as_of_round: int, n_next: int = 5) -> pd.DataFrame:
    """Next N opponents of every team and the average opponent DRtg (strength of schedule)."""
    s = upcoming_games(season, as_of_round, n_next)
    if s.empty:
        return s
    s = s.merge(q("SELECT club_code, tag FROM dim_team"), on="club_code", how="left")
    s["opp_drtg"] = s.opp_code.map(team_drtg(season, as_of_round).set_index("club_code").drtg)
    s["label"] = s.opp + " (" + s.venue + ")"
    return s
