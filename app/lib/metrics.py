"""THE metric catalog. Every number in the app is defined here exactly once.

Each metric is a SQL expression over *component sums* (see COMPONENTS), so the
same definition works for any filter: a season, a round range, home games only...
This replaces the ~445 DAX measures of the Power BI model.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    sql: str
    fmt: str            # 'int' | 'dec1' | 'dec2' | 'pct'
    group: str          # Box score | Per game | Shooting | Advanced | Fantasy
    desc: str


# ---------- component sums (player level) ----------
# p = fct_player_game, t = fct_team_game of the player's team in that game.
# Team/opponent totals only count games the player actually played.
PLAYER_COMPONENTS = {
    "gp": "sum(p.played)",
    "wins": "sum(p.played * (p.wl = 'W')::INT)",
    "starts": "sum(p.is_starter)",
    "min": "sum(p.minutes)",
    **{c: f"sum(p.{c})" for c in
       "pts fg2m fg2a fg3m fg3a fgm fga ftm fta oreb dreb reb ast stl tov blk blka pf fd pir fp fp_win poss".split()},
    **{f"tm_{c}": f"sum(t.{c} * p.played)" for c in "minutes fgm fga fta oreb dreb tov poss".split()},
    **{f"op_{c}": f"sum(t.opp_{c} * p.played)" for c in "pts fg2a oreb dreb poss".split()},
}

PLAYER_METRICS = [
    # --- box score totals ---
    Metric("gp", "GP", "gp", "int", "Box score", "Games played (minutes > 0)."),
    Metric("w", "W", "wins", "int", "Box score", "Wins in games played."),
    Metric("l", "L", "gp - wins", "int", "Box score", "Losses in games played."),
    Metric("gs", "GS", "starts", "int", "Box score", "Games started."),
    Metric("min", "MIN", "min", "dec1", "Box score", "Minutes played."),
    Metric("pts", "PTS", "pts", "int", "Box score", "Points."),
    Metric("fg2m", "2PTM", "fg2m", "int", "Box score", "2-point field goals made."),
    Metric("fg2a", "2PTA", "fg2a", "int", "Box score", "2-point field goals attempted."),
    Metric("fg3m", "3PTM", "fg3m", "int", "Box score", "3-point field goals made."),
    Metric("fg3a", "3PTA", "fg3a", "int", "Box score", "3-point field goals attempted."),
    Metric("fgm", "FGM", "fgm", "int", "Box score", "Field goals made."),
    Metric("fga", "FGA", "fga", "int", "Box score", "Field goals attempted."),
    Metric("ftm", "FTM", "ftm", "int", "Box score", "Free throws made."),
    Metric("fta", "FTA", "fta", "int", "Box score", "Free throws attempted."),
    Metric("oreb", "OR", "oreb", "int", "Box score", "Offensive rebounds."),
    Metric("dreb", "DR", "dreb", "int", "Box score", "Defensive rebounds."),
    Metric("reb", "TR", "reb", "int", "Box score", "Total rebounds."),
    Metric("ast", "AST", "ast", "int", "Box score", "Assists."),
    Metric("stl", "STL", "stl", "int", "Box score", "Steals."),
    Metric("tov", "TO", "tov", "int", "Box score", "Turnovers."),
    Metric("blk", "BLK", "blk", "int", "Box score", "Blocks made (in favour)."),
    Metric("blka", "BLKA", "blka", "int", "Box score", "Shots of the player that were blocked (against)."),
    Metric("pf", "PF", "pf", "int", "Box score", "Personal fouls committed."),
    Metric("fd", "FD", "fd", "int", "Box score", "Fouls drawn."),
    Metric("pir", "PIR", "pir", "int", "Box score", "Official EuroLeague Performance Index Rating (VAL)."),
    # --- per game ---
    *[Metric(f"{k}_pg", f"{lab}/G", f"{k} / nullif(gp, 0)", "dec1", "Per game", f"{lab} per game played.")
      for k, lab in [("min", "MIN"), ("pts", "PTS"), ("reb", "TR"), ("oreb", "OR"), ("dreb", "DR"),
                     ("ast", "AST"), ("stl", "STL"), ("tov", "TO"), ("blk", "BLK"), ("fg2m", "2PTM"),
                     ("fg3m", "3PTM"), ("ftm", "FTM"), ("fga", "FGA"), ("fta", "FTA"), ("pf", "PF"),
                     ("fd", "FD"), ("pir", "PIR")]],
    Metric("fgm_pg", "FGM/G", "fgm / nullif(gp, 0)", "dec1", "Per game", "Field goals made per game."),
    Metric("missed_fg_pg", "Missed FG/G", "(fga - fgm) / nullif(gp, 0)", "dec1", "Per game", "Missed field goals per game."),
    Metric("missed_ft_pg", "Missed FT/G", "(fta - ftm) / nullif(gp, 0)", "dec1", "Per game", "Missed free throws per game."),
    Metric("ball_handling", "Playmaking", "(2 * ast - tov) / nullif(gp, 0)", "dec1", "Per game",
           "2 × assists − turnovers, per game (from 'Ball Handling Metric')."),
    # --- shooting ---
    Metric("fg2_pct", "2PT%", "fg2m / nullif(fg2a, 0)", "pct", "Shooting", "2-point percentage."),
    Metric("fg3_pct", "3PT%", "fg3m / nullif(fg3a, 0)", "pct", "Shooting", "3-point percentage."),
    Metric("fg_pct", "FG%", "fgm / nullif(fga, 0)", "pct", "Shooting", "Field goal percentage."),
    Metric("ft_pct", "FT%", "ftm / nullif(fta, 0)", "pct", "Shooting", "Free throw percentage."),
    Metric("efg_pct", "eFG%", "(fgm + 0.5 * fg3m) / nullif(fga, 0)", "pct", "Shooting",
           "Effective FG%: a made three counts 1.5 field goals."),
    Metric("ts_pct", "TS%", "pts / nullif(2 * (fga + 0.44 * fta), 0)", "pct", "Shooting",
           "True shooting %: scoring efficiency including free throws."),
    Metric("fg3a_rate", "3PA rate", "fg3a / nullif(fga, 0)", "pct", "Shooting", "Share of field goal attempts that are threes."),
    Metric("fta_rate", "FT rate", "fta / nullif(fga, 0)", "dec2", "Shooting", "Free throw attempts per field goal attempt (0.36 = 36 FTA per 100 FGA)."),
    Metric("pts_per_min", "PTS/MIN", "pts / nullif(min, 0)", "dec2", "Shooting", "Points per minute."),
    # --- advanced ---
    Metric("usg_pct", "USG%",
           "(fga + 0.44 * fta + tov) * (tm_minutes / 5) / nullif(min * (tm_fga + 0.44 * tm_fta + tm_tov), 0)",
           "pct", "Advanced", "Usage: share of team possessions used by the player while on court."),
    Metric("ast_pct", "AST%", "least(1, ast / nullif((min / nullif(tm_minutes / 5, 0)) * tm_fgm - fgm, 0))", "pct",
           "Advanced", "Share of teammates' field goals the player assisted while on court. On very few minutes the "
           "estimate can pass 100%, so it is capped at 100%."),
    Metric("tov_pct", "TOV%", "tov / nullif(fga + 0.44 * fta + tov, 0)", "pct", "Advanced",
           "Turnovers per individual play."),
    Metric("ast_to", "AST/TO", "ast / nullif(tov, 0)", "dec2", "Advanced", "Assists per turnover."),
    Metric("orb_pct", "ORB%", "oreb * (tm_minutes / 5) / nullif(min * (tm_oreb + op_dreb), 0)", "pct", "Advanced",
           "Share of available offensive rebounds grabbed while on court."),
    Metric("drb_pct", "DRB%", "dreb * (tm_minutes / 5) / nullif(min * (tm_dreb + op_oreb), 0)", "pct", "Advanced",
           "Share of available defensive rebounds grabbed while on court."),
    Metric("trb_pct", "TRB%", "reb * (tm_minutes / 5) / nullif(min * (tm_oreb + tm_dreb + op_oreb + op_dreb), 0)",
           "pct", "Advanced", "Share of all available rebounds grabbed while on court."),
    Metric("stl_pct", "STL%", "stl * (tm_minutes / 5) / nullif(min * op_poss, 0)", "pct", "Advanced",
           "Steals per opponent possession while on court."),
    Metric("blk_pct", "BLK%", "blk * (tm_minutes / 5) / nullif(min * op_fg2a, 0)", "pct", "Advanced",
           "Share of opponent 2-point attempts blocked while on court."),
    Metric("off_rtg", "Indiv. ORtg", "100 * pts / nullif(poss, 0)", "dec1", "Advanced",
           "Points per 100 individual possessions (FGA + 0.44·FTA − OR + TO)."),
    Metric("team_drtg_on", "Team DRtg (played)", "100 * op_pts / nullif(op_poss, 0)", "dec1", "Advanced",
           "Team defensive rating in the games the player played. Replaces the old player 'DRtg', "
           "which actually summed the player's own points in losses."),
    Metric("eff", "EFF", "(pts + reb + ast + stl + blk - (fga - fgm) - (fta - ftm) - tov) / nullif(gp, 0)", "dec1",
           "Advanced", "NBA-style efficiency per game (was 'PER II' - it is not PER)."),
    Metric("uper", "uPER (per min)",
           "(pts + 0.4 * fgm - 0.7 * fga - 0.4 * (fta - ftm) + 0.7 * oreb + 0.3 * dreb + stl + 0.7 * ast "
           "+ 0.7 * blk - 0.4 * pf - tov) / nullif(min, 0)", "dec2", "Advanced",
           "Simplified unadjusted PER per minute (formula of the old 'UnadjustedPERR').")
    ,
    Metric("net_rtg_ind", "Indiv. Net", "100 * pts / nullif(poss, 0) - 100 * op_pts / nullif(op_poss, 0)", "dec1",
           "Advanced", "Individual ORtg − team DRtg in games played. The old 'P Net Rating' divided ORtg by a "
           "broken DRtg; this is the difference, as the note in that measure intended."),
    # --- fantasy ---
    Metric("fp", "FP", "fp", "dec1", "Fantasy", "Fantasy points = official PIR."),
    Metric("fp_pg", "FP/G", "fp / nullif(gp, 0)", "dec1", "Fantasy", "Fantasy points per game played."),
    Metric("fp_win", "FP (win bonus)", "fp_win", "dec1", "Fantasy", "Fantasy points with +10% on wins."),
    Metric("fp_win_pg", "FP+W/G", "fp_win / nullif(gp, 0)", "dec1", "Fantasy", "Win-bonus fantasy points per game."),
]

# ---------- team level (t = fct_team_game) ----------
TEAM_COMPONENTS = {
    "gp": "count(*)",
    "wins": "sum((t.wl = 'W')::INT)",
    "home_w": "sum((t.wl = 'W' AND t.venue = 'H')::INT)",
    "home_l": "sum((t.wl = 'L' AND t.venue = 'H')::INT)",
    "away_w": "sum((t.wl = 'W' AND t.venue = 'A')::INT)",
    "away_l": "sum((t.wl = 'L' AND t.venue = 'A')::INT)",
    "min": "sum(t.minutes)",
    **{c: f"sum(t.{c})" for c in
       "pts fg2m fg2a fg3m fg3a fgm fga ftm fta oreb dreb reb ast stl tov blk blka pf fd pir poss margin".split()},
    **{f"op_{c}": f"sum(t.opp_{c})" for c in
       "pts fgm fga fg3m fg3a ftm fta oreb dreb reb ast stl tov blk poss pir".split()},
}

TEAM_METRICS = [
    Metric("gp", "GP", "gp", "int", "Record", "Games played."),
    Metric("w", "W", "wins", "int", "Record", "Wins."),
    Metric("l", "L", "gp - wins", "int", "Record", "Losses."),
    Metric("win_pct", "W%", "wins / nullif(gp, 0)", "pct", "Record", "Winning percentage."),
    Metric("home", "Home", "home_w || '-' || home_l", "text", "Record", "Home record."),
    Metric("away", "Away", "away_w || '-' || away_l", "text", "Record", "Away record."),
    Metric("pts_pg", "PTS/G", "pts / gp", "dec1", "Per game", "Points scored per game."),
    Metric("opp_pts_pg", "OPP PTS/G", "op_pts / gp", "dec1", "Per game", "Points allowed per game."),
    Metric("margin_pg", "+/-", "margin / gp", "dec1", "Per game", "Average point differential."),
    *[Metric(f"{k}_pg", f"{lab}/G", f"{k} / gp", "dec1", "Per game", f"{lab} per game.")
      for k, lab in [("fgm", "FGM"), ("fg2m", "2PTM"), ("fg3m", "3PTM"), ("ftm", "FTM"), ("oreb", "OR"),
                     ("dreb", "DR"), ("reb", "TR"), ("ast", "AST"), ("stl", "STL"), ("tov", "TO"),
                     ("blk", "BLK"), ("blka", "BLKA"), ("pf", "PF"), ("fd", "FD"), ("pir", "PIR")]],
    Metric("fg2_pct", "2PT%", "fg2m / nullif(fg2a, 0)", "pct", "Shooting", "2-point percentage."),
    Metric("fg3_pct", "3PT%", "fg3m / nullif(fg3a, 0)", "pct", "Shooting", "3-point percentage."),
    Metric("ft_pct", "FT%", "ftm / nullif(fta, 0)", "pct", "Shooting", "Free throw percentage."),
    Metric("efg_pct", "eFG%", "(fgm + 0.5 * fg3m) / nullif(fga, 0)", "pct", "Shooting", "Effective FG%."),
    Metric("ts_pct", "TS%", "pts / nullif(2 * (fga + 0.44 * fta), 0)", "pct", "Shooting", "True shooting %."),
    Metric("fg3a_rate", "3PA rate", "fg3a / nullif(fga, 0)", "pct", "Shooting", "Share of attempts from three."),
    Metric("pace", "Pace", "((poss + op_poss) / 2) * 200 / nullif(min, 0)", "dec1", "Advanced",
           "Possessions per 40 minutes."),
    Metric("off_rtg", "ORtg", "100 * pts / nullif(poss, 0)", "dec1", "Advanced", "Points scored per 100 possessions."),
    Metric("def_rtg", "DRtg", "100 * op_pts / nullif(op_poss, 0)", "dec1", "Advanced",
           "Points allowed per 100 opponent possessions (higher = weaker defense)."),
    Metric("net_rtg", "NetRtg", "100 * pts / nullif(poss, 0) - 100 * op_pts / nullif(op_poss, 0)", "dec1",
           "Advanced", "ORtg − DRtg."),
    Metric("tov_pct", "TOV%", "tov / nullif(fga + 0.44 * fta + tov, 0)", "pct", "Four factors", "Turnovers per play."),
    Metric("orb_pct", "ORB%", "oreb / nullif(oreb + op_dreb, 0)", "pct", "Four factors", "Offensive rebound %."),
    Metric("drb_pct", "DRB%", "dreb / nullif(dreb + op_oreb, 0)", "pct", "Four factors", "Defensive rebound %."),
    Metric("fta_rate", "FT rate", "fta / nullif(fga, 0)", "dec2", "Four factors", "FTA per FGA."),
    Metric("opp_orb_pct", "Opp ORB%", "op_oreb / nullif(op_oreb + dreb, 0)", "pct", "Four factors",
           "Offensive rebound % of the opponents (lower = better defensive rebounding)."),
    Metric("opp_fta_rate", "Opp FT rate", "op_fta / nullif(op_fga, 0)", "dec2", "Four factors",
           "Opponent FTA per FGA (lower = fewer fouls)."),
    Metric("opp_efg_pct", "Opp eFG%", "(op_fgm + 0.5 * op_fg3m) / nullif(op_fga, 0)", "pct", "Four factors",
           "Opponent effective FG%."),
    Metric("opp_tov_pct", "Opp TOV%", "op_tov / nullif(op_fga + 0.44 * op_fta + op_tov, 0)", "pct", "Four factors",
           "Opponent turnovers per play."),
    Metric("ast_ratio", "AST/FGM", "ast / nullif(fgm, 0)", "pct", "Advanced", "Share of made shots that were assisted."),
    Metric("fp_pg", "Team FP/G", "pir / gp", "dec1", "Fantasy", "Team PIR per game (incl. team rebounds)."),
    Metric("eff_pg", "Team EFF/G", "(pts + reb + ast + stl + blk - (fga - fgm) - (fta - ftm) - tov) / gp", "dec1",
           "Fantasy", "Team NBA-style efficiency per game (old 'Team EFF NBA II' / 'Team PER II')."),
    Metric("pts_total", "Team points", "pts", "int", "Record", "Points scored (old 'Team Points')."),
    Metric("opp_pts_total", "Opp points", "op_pts", "int", "Record", "Points allowed (old 'Opponent Points')."),
]

PLAYER = {m.key: m for m in PLAYER_METRICS}
TEAM = {m.key: m for m in TEAM_METRICS}
