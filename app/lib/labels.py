"""Column titles, hover explanations and formats for every fantasy table — defined once.
show(df, columns) renders a table with these, '—' for empty cells and 'needs more games' labels."""
import pandas as pd
import streamlit as st

# key: (title, hover help, format)   formats: text | int | d1 | d2 | pct | pct100 | signed | score
COLS = {
    # identity
    "player_name": ("PLAYER", "", "text"),
    "team": ("TEAM", "", "text"),
    "position": ("POS", "G = guard, F = forward, C = center", "text"),
    "price": ("PRICE", "Current fantasy price in credits", "d1"),
    "prev_price": ("PREV PRICE", "Price before the last round", "d1"),
    "gp": ("GP", "Games played", "int"),
    "min_pg": ("MIN/G", "Minutes per game", "d1"),
    # fantasy points
    "fp_pg": ("FP/G", "Fantasy points (= PIR) per game this season", "d1"),
    "fp_l1": ("FP L1", "Fantasy points in the last game played", "d1"),
    "fp_l1_round": ("L1 ROUND", "Round of the last game played", "int"),
    "fp_l1_win": ("FP L1 +WIN", "Last game's fantasy points including the +10% win bonus", "d1"),
    "fp_l3": ("FP L3", "Average fantasy points over the last 3 games played", "d1"),
    "fp_l5": ("FP L5", "Average fantasy points over the last 5 games played", "d1"),
    "fp_win_pg": ("FP+W/G", "Fantasy points per game including the +10% win bonus", "d1"),
    "pts_l3": ("PTS L3", "Average points scored over the last 3 games", "d1"),
    "pir_l3": ("PIR L3", "Average PIR over the last 3 games", "d1"),
    "exp_fp_next": ("EXP FP NEXT", "Expected fantasy points in the next game, win bonus included. Recent level "
                    "(50% last 3 + 30% season + 20% last 5), blended with his price early in the season, × how many "
                    "FP the opponent allows to his position × home/away × (1 + 10% × win chance) × injury status", "d1"),
    "exp_fp_base": ("BASE FP", "His expected level before the matchup: recent FP blended with his price "
                    "(own games weigh n/(n+2))", "d1"),
    "next_win_prob": ("WIN %", "Chance his team wins the next game (same model as the coaches)", "pct"),
    "exp_value": ("EXP FP / CR", "Expected FP next game per credit", "d2"),
    "matchup_factor": ("MATCHUP ×", "How generous the next opponent is to this position. 1.10 = gives up 10% more FP "
                       "than average, 0.90 = 10% less", "d2"),
    # scores
    "pick_score": ("PICK SCORE", "0-100. 50% expected FP next + 30% expected FP per credit + 20% safety, each "
                   "ranked against all eligible players. The matchup is already inside Exp FP", "score"),
    "pick_tier": ("TIER", "From PICK + INJ: 🔥 ≥85 · 🟢 ≥70 · 🟡 ≥55 · 🔴 below · ⛔ injured", "text"),
    "pick_score_inj": ("PICK + INJ", "Pick score (the injury opportunity is inside Exp FP). Injured players get no score", "int"),
    "inj_boost": ("INJ BOOST", "Extra expected FP because injured teammates at his position free up minutes: their "
                  "minutes (out 100%, doubtful 50%, 0 if they already missed the last 2 games) are shared among the "
                  "healthy players of that position in proportion to their minutes (max +12 min, 34 in total), × his FP "
                  "per minute × 0.85. Already included in Exp FP", "d1"),
    "good_score": ("GOOD", "Good score 0-100 (higher = better) over his last 3 games: fantasy points, points above his "
                   "break-even, jump vs his season, scoring, assists + rebounds, true shooting and minutes — ranked "
                   "against his position", "int"),
    "good_label": ("FORM", "🔥 Hot = good score 80+ · 🟢 Good = 65-80", "text"),
    "good_reasons": ("WHY", "His two strongest areas in the last games", "text"),
    "good_fp": ("FP L3", "Fantasy points per game in his last 3 games", "d1"),
    "good_games": ("GAMES", "Games counted (of his last 3)", "int"),
    "good_over_be": ("VS BREAK-EVEN", "FP last 3 − 1.1 × price: above 0 his price goes up", "signed"),
    "good_jump": ("VS SEASON", "FP last 3 − FP per game of the season", "signed"),
    "good_pts30": ("PTS /30", "Points per 30 minutes (last 3)", "d1"),
    "good_astreb30": ("AST+REB /30", "Assists + rebounds per 30 minutes (last 3)", "d1"),
    "good_ts": ("TS%", "True shooting % (last 3)", "d1"),
    "good_min_jump": ("MIN Δ", "Minutes per game last 3 − season", "signed"),
    "bad_score": ("BAD", "Bad score 0-100 (higher = worse): missed free throws, missed shots, fouls, turnovers, few "
                  "assists and few points per 30 minutes, ranked against his position. 60% last 5 games + 40% season",
                  "int"),
    "bad_label": ("FORM", "🔴 Bad form = bad score 70+ · 🟠 Watch = 60-70 · 🟢 OK", "text"),
    "bad_reasons": ("WHY", "His two worst areas recently", "text"),
    "bad_season": ("BAD SEASON", "Bad score over the whole season", "int"),
    "bad_recent": ("BAD L5", "Bad score over the last 5 games", "int"),
    "bad_miss_ft": ("MISSED FT /30", "Missed free throws per 30 minutes (last 5)", "d1"),
    "bad_miss_fg": ("MISSED FG /30", "Missed field goals per 30 minutes (last 5)", "d1"),
    "bad_pf": ("FOULS /30", "Fouls committed per 30 minutes (last 5)", "d1"),
    "bad_tov": ("TO /30", "Turnovers per 30 minutes (last 5)", "d1"),
    "bad_ast": ("AST /30", "Assists per 30 minutes (last 5)", "d1"),
    "bad_pts": ("PTS /30", "Points per 30 minutes (last 5)", "d1"),
    "price_up_prob": ("P(▲)", "Chance his price goes UP after this round: his fantasy points must reach 1.1 × price "
                      "(with the win bonus). From his expected points and his game-to-game spread", "pct"),
    "price_tonight": ("PRICE TONIGHT", "Price change after this round: ✓ = already decided (he has played), "
                      "otherwise the expected change and the chance of a rise", "text"),
    "selected_pct": ("POP%", "Popularity: share of ALL the game's managers who own him (from the game)", "d1"),
    "selected_change": ("POP Δ", "Change of that share since the previous round, in percentage points: "
                        "▲ = managers are buying him, ▼ = selling", "signed"),
    "game_prob": ("GAME AVAIL", "The game's own probability of playing: 1 = fit, 0.5 = doubtful, 0 = out", "d1"),
    "gbl_gp": ("GBL GP", "Games played in the Greek league (Panathinaikos / Olympiacos players)", "int"),
    "gbl_min_pg": ("GBL MIN", "Minutes per game in the Greek league", "d1"),
    "gbl_min_7d": ("GBL MIN 7D", "Minutes in the Greek league in the last 7 days", "d1"),
    "gbl_flag": ("GBL LOAD", "⚠️ = 25+ minutes in a Greek league game up to 3 days before his next EuroLeague game "
                 "(tired legs, rotation risk) · 💤 = a regular who rested in that Greek game (fresh for the "
                 "EuroLeague) · — = played in the GBL, nothing special", "text"),
    "inj_extra_min": ("INJ +MIN", "Extra minutes per game expected because teammates at his position are out", "d1"),
    "inj_teammates": ("INJ TEAMMATES", "Recently injured/doubtful teammates at the same position who were playing", "int"),
    "hot_tier": ("HOT", "Short-term heat: 50% FP last 3 + 30% form + 20% value. 💥 top 5 · 🔥 top 15 · 🟡 top 30 · "
                 "— the rest", "text"),
    "perf_score": ("PERF", "0-100 rank of expected FP next game", "int"),
    "value_score": ("VALUE", "0-100 rank of expected FP next game per credit", "int"),
    "risk_score": ("SAFETY", "0-100: steady minutes, stable role and availability. Higher = safer", "int"),
    "schedule_score": ("MATCHUP", "0-100 rank of the matchup factor. Higher = easier", "int"),
    "ceiling_score": ("CEILING", "0-100 rank of his best game in the last 5", "int"),
    "value_l3": ("FP L3 / CR", "Fantasy points (last 3) per credit of price", "d2"),
    "value_total": ("SEASON FP / CR", "Season fantasy points per credit", "d2"),
    "value_rank": ("VALUE RANK", "Rank by season FP per credit", "int"),
    "captain_score": ("CAPTAIN SCORE", "0-100. 60% expected FP next + 25% ceiling (best game of the last 5) + "
                      "15% safety. Price does not matter for the captain", "score"),
    "captain_tier": ("CAPTAIN", "👑 ≥85 · ⭐ ≥70 · ✅ ≥55", "text"),
    "captain_rank": ("RANK", "Captain rank", "int"),
    "wildcard": ("WILDCARD", "Cheap (≤11 cr) with pick score ≥55 and value ≥65. 🟢 if also safe (≥50)", "text"),
    # form
    "form_index": ("FORM", "Fantasy points per minute in the last 3 games ÷ season. 1.00 = normal, 1.20 = 20% "
                   "better than usual. Needs ≥30 season minutes and ≥20 in the last 3", "d2"),
    "form_label": ("FORM STATUS", "🚀 super hot · 🔥 hot · ⚪ normal · 🧊 cold", "text"),
    "spike_l1": ("L1 SPIKE", "Last game's FP ÷ season FP/G. 1.50 = 50% above his average", "d2"),
    "l1_label": ("LAST GAME", "💥 explosion · 🔥 big game · ⚪ normal · 🧊 poor", "text"),
    "potential": ("UPSIDE", "Usage × minutes × softness of the next opponent — ceiling for the next game", "d1"),
    "min_l3": ("MIN L3", "Minutes per game, last 3 games", "d1"),
    "min_l5": ("MIN L5", "Minutes per game, last 5 games", "d1"),
    "min_l3_total": ("MIN L3 TOT", "Total minutes in the last 3 games", "d1"),
    "min_trend": ("MIN TREND", "Minutes last 3 ÷ season. >1 = getting more minutes", "d2"),
    "expected_min": ("EXP MIN", "Expected minutes (last-5 average, capped 15-32)", "d1"),
    "minutes_factor": ("MIN FACTOR", "Expected minutes ÷ 30", "d2"),
    "usg_season": ("USG%", "Share of team possessions he uses while on court (season)", "pct"),
    "usg_l3": ("USG% L3", "Usage over the last 3 games", "pct"),
    "usg_trend": ("USG TREND", "Usage last 3 ÷ season. >1 = more involved", "d2"),
    "usage_vs_min_delta": ("USG−MIN Δ", "Usage change minus minutes change. Positive = more involved per minute", "d2"),
    "eff_trend": ("EFF TREND", "Efficiency of the last 2 games minus the 2 before", "signed"),
    "eff_rank_round": ("EFF RANK LAST RD", "Rank by efficiency in the last round", "int"),
    "win_impact": ("WIN IMPACT", "Average FP in wins minus average FP in losses", "signed"),
    # risk
    "role_stability": ("ROLE", "Share of games with 20+ minutes. Needs 6 games", "pct"),
    "role_label": ("ROLE STATUS", "🟢 locked starter · 🟡 stable rotation · 🟠 fringe · 🔴 unstable", "text"),
    "min_volatility": ("MIN SWING", "How much his minutes jump around (lower = steadier)", "d2"),
    "perf_stability": ("CONSISTENCY", "1 = same FP every game, lower = boom-or-bust. Needs 6 games", "d2"),
    "dnp_season": ("DNP", "Team games he missed this season", "int"),
    "dnp_l3": ("DNP L3", "Team games missed out of the last 3", "int"),
    "availability": ("AVAILABILITY", "Share of team games he played", "pct"),
    "injury_prob": ("MISS RATE", "Share of team games missed", "pct"),
    "durability_label": ("DURABILITY", "🟢 ≤10% missed · 🟡 ≤30% · 🔴 more", "text"),
    "injury_level": ("INJ LEVEL", "0 healthy · 1 minor · 2 questionable · 3 doubtful · 4 out", "int"),
    "injury_label": ("INJURY", "From the injury report", "text"),
    "inj_flag": ("INJ", "OUT = out · Q = doubtful/questionable · OK", "text"),
    "inj_status": ("STATUS", "Status in the injury report", "text"),
    "inj_note": ("NOTE", "Comment in the injury report", "text"),
    "risk_label": ("SAFETY STATUS", "🟢 safe · 🟡 medium · 🟠 risky · 🔴 high risk", "text"),
    # schedule
    "next_round": ("NEXT RD", "Round of the next game", "int"),
    "next_opp": ("NEXT OPP", "Next opponent", "text"),
    "next_venue": ("H/A", "H = home, A = away", "text"),
    "next_opp_drtg": ("OPP DRTG", "Next opponent's points allowed per 100 possessions. Higher = weaker defense", "d1"),
    "schedule_band": ("MATCHUP", "Easy / Medium / Tough, by the next opponent's defensive rating", "text"),
    "next_opp_pts_allowed_pos": ("OPP PTS→POS", "Points the next opponent allows per player of this position", "d1"),
    "next_opp_fp_allowed_pos": ("OPP FP→POS", "Fantasy points the next opponent allows per player of this position", "d1"),
    # market
    "price_change": ("LAST CHANGE", "Price change after the last round", "signed"),
    "exp_price_move": ("EXP MOVE", "Expected price change after the next game, from the game's rule: "
                       "(FP with win bonus − 1.1 × price) ÷ 25, using Exp FP next. Never below 4.0", "signed"),
    "break_even": ("BREAK-EVEN", "FP (win bonus included) he needs next game to keep his price = 1.1 × price", "d1"),
    "fair_price": ("FAIR PRICE", "The price at which he would stop rising or falling, given his expected level", "d1"),
    "price_gap": ("GAP", "Fair price − price. Positive = underpriced, likely to rise", "signed"),
}

# text shown when a label cannot be computed yet
FILL = {"form_label": "⏳ needs minutes", "l1_label": "—", "role_label": "⏳ needs 6 games",
        "risk_label": "⏳ not ranked yet", "pick_tier": "⏳ not ranked yet", "hot_tier": "—",
        "captain_tier": "—", "wildcard": "", "durability_label": "—", "schedule_band": "—",
        "injury_label": "🟢 Healthy", "inj_flag": "OK", "inj_status": "", "inj_note": ""}

_FMT = {"int": "{:.0f}", "d1": "{:.1f}", "d2": "{:.2f}", "pct": "{:.0%}", "signed": "{:+.1f}"}
_SMALL = {"ii", "iii", "iv", "jr", "jr.", "sr", "sr."}
_PARTICLES = {"de", "da", "di", "du", "le", "la", "el", "al", "jo", "bo", "ty", "al"}


def pname(name) -> str:
    """'SHORTS, TJ' -> 'Shorts, TJ' · 'BALDWIN IV, WADE' -> 'Baldwin IV, Wade'."""
    if not isinstance(name, str):
        return name
    out, first = [], False
    for w in name.replace(",", " , ").split():
        if w == ",":
            out.append(","); first = True
        elif w.lower().strip(".") in {x.strip(".") for x in _SMALL} or (len(w) <= 4 and "." in w) \
                or (first and len(w) == 2 and w.isalpha() and w.lower() not in _PARTICLES):
            out.append(w.upper())
        else:
            out.append(w.title())
    return " ".join(out).replace(" ,", ",")


def arrow(v, fmt="{:+.1f}") -> str:
    """▲ +0.9 / ▼ −0.4 / – (for up/down columns)."""
    if v is None or v != v:
        return ""
    if abs(v) < 1e-9:
        return "–"
    return ("▲ " if v > 0 else "▼ ") + fmt.format(v).replace("-", "−")


def arrow_color(v) -> str:
    if isinstance(v, str) and v.startswith("▲"):
        return "color: #15803d; font-weight: 600"
    if isinstance(v, str) and v.startswith("▼"):
        return "color: #dc2626; font-weight: 600"
    return ""


def show(df: pd.DataFrame, cols: list, height: int = 640, extra: dict | None = None, arrows: list | None = None,
         drop_empty: bool = False, colors: dict | None = None):
    """Render a fantasy table with uppercase titles, hover help and '—' for missing values.
    arrows: columns shown as ▲/▼ with green/red. drop_empty: hide columns with no values at all
    (e.g. form metrics before round 3). colors: {column: {value: css}} for labels such as Easy/Hard."""
    extra = extra or {}
    arrows = [c for c in (arrows or []) if c in df]
    cols = [c for c in cols if c in df]
    if drop_empty:
        cols = [c for c in cols if df[c].notna().any() and not (df[c].astype(str).isin(["", "None", "nan"]).all())]
    arrows = [c for c in arrows if c in cols]
    d = df[cols].copy()
    if "player_name" in d:
        d["player_name"] = d.player_name.map(pname)
    for c in cols:
        if c in FILL:
            d[c] = d[c].astype("object").where(d[c].notna(), FILL[c])
    kinds = {c: extra.get(c, COLS.get(c, (c, "", "text")))[2] for c in cols}
    for c in arrows:
        d[c] = d[c].map(lambda v, k=kinds[c]: arrow(v, "{:+.2f}" if k == "d2" else "{:+.1f}"))
    fmt = {c: _FMT[k] for c, k in kinds.items() if k in _FMT and c not in arrows}
    styler = d.style.format(fmt, na_rep="—")
    if arrows:
        styler = styler.map(arrow_color, subset=arrows)
    for c, m in (colors or {}).items():
        if c in d:
            styler = styler.map(lambda v, m=m: m.get(v, ""), subset=[c])
    cfg = {}
    for c in cols:
        title, helptext, kind = extra.get(c, COLS.get(c, (c.upper(), "", "text")))
        if kind == "score" and c not in arrows:
            cfg[c] = st.column_config.ProgressColumn(title, help=helptext or None, min_value=0, max_value=100, format="%d")
        elif kind == "text" or c in arrows:
            cfg[c] = st.column_config.TextColumn(title, help=helptext or None)
        else:
            cfg[c] = st.column_config.Column(title, help=helptext or None)
    st.dataframe(styler, column_config=cfg, hide_index=True, width="stretch", height=height)


# colours for Easy / Medium / Hard labels
BAND_COLORS = {"Easy": "color: #15803d; font-weight: 600", "Medium": "color: #b45309",
               "Tough": "color: #dc2626; font-weight: 600", "Hard": "color: #dc2626; font-weight: 600"}
