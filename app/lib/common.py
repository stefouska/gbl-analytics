"""Shared fantasy context for the new pages (cached)."""
import hashlib
import math
import os
import re

import numpy as np
import pandas as pd
from pathlib import Path

import streamlit as st

from . import db, fantasy, ui


def season_and_round(key: str, label="As of round"):
    seasons = ui._seasons()
    season = st.sidebar.selectbox("Season", seasons, format_func=lambda s: f"{s}-{str(s + 1)[-2:]}", key=f"{key}_s")
    rnds = ui._rounds(season)
    as_of = st.sidebar.select_slider(label, rnds, value=rnds[-1], key=f"{key}_r") if len(rnds) > 1 else rnds[-1]
    ui.footer()
    return season, as_of


# Fingerprint of the calculation code + the data file: when either changes (an update is uploaded,
# or the daily refresh runs) the cached results are thrown away automatically.
def _version() -> str:
    h = hashlib.md5()
    for f in sorted(Path(__file__).parent.glob("*.py")):
        h.update(f.read_bytes())
    wh = db.db_path()
    h.update(str(wh.stat().st_mtime if wh.exists() else 0).encode())
    return h.hexdigest()


@st.cache_data(ttl=3600)
def _board(season: int, as_of: int, version: str):
    return fantasy.player_board(db.player_games(season, as_of), season, as_of)


@st.cache_data(ttl=3600)
def _coaches(season: int, as_of: int, version: str):
    return fantasy.coach_board(season, as_of)


@st.cache_data(ttl=3600)
def _projections(season: int, as_of: int, version: str):
    b = _board(season, as_of, version)
    return fantasy.game_projections(b, season, as_of, 4)


def projections(season: int, as_of: int):
    """Expected FP of every player in each of his team's next 4 games (round, turn, opponent, exp_fp)."""
    return _projections(season, as_of, _version())


def rounds_info(season: int) -> dict:
    return fantasy.round_status(season)


def board_for_round(season: int, as_of: int, rnd: int):
    """The player board plus, for round `rnd`: exp_round (expected FP in that round, 0 if his team has already
    played it), turn, opponent and venue of that game, and the price move that game would bring."""
    b = board(season, as_of).copy()
    p = projections(season, as_of)
    pr = p[p["round"] == rnd].drop_duplicates("player_id").set_index("player_id") if not p.empty else None
    if pr is None or pr.empty:
        b["exp_round"], b["turn"], b["round_opp"], b["round_venue"] = np.nan, np.nan, None, None
        b["round_date"], b["round_time"] = pd.NaT, None
    else:
        b["exp_round"] = b.player_id.map(pr.exp_fp)
        b["turn"] = b.player_id.map(pr.turn)
        b["round_opp"] = b.player_id.map(pr.opp)
        b["round_venue"] = b.player_id.map(pr.venue)
        b["round_date"] = b.player_id.map(pr.game_date)
        b["round_time"] = b.player_id.map(pr.start_time)
    b["turn_label"] = b.turn.map(lambda t: f"T{int(t)}" if t == t else "—")
    b["exp_round"] = b.exp_round.fillna(0.0)
    b["exp_round_value"] = b.exp_round / b.price
    b["exp_round_move"] = ((b.exp_round - 1.1 * b.price) / 25).round(1).clip(lower=fantasy.MIN_PRICE - b.price)
    # chance that his price goes UP after this round: P(fantasy points ≥ 1.1 × price), points ~ normal around
    # the expected points with his own game-to-game spread (or 45% of the expectation + 3 when he has < 3 games)
    sd = b.fp_sd.where(b.fp_sd > 0) if "fp_sd" in b else pd.Series(np.nan, index=b.index)
    sd = sd.fillna(0.45 * b.exp_round.clip(lower=5) + 3).clip(lower=3)
    z = (b.exp_round - 1.1 * b.price) / sd
    b["price_up_prob"] = np.where(b.exp_round > 0, 0.5 * (1 + np.vectorize(math.erf)(z / math.sqrt(2))), np.nan)
    # smart = this round at full weight + the next two games at half weight (for transfers you keep)
    later = p[p["round"] > rnd].sort_values("k").groupby("player_id").head(2).groupby("player_id").exp_fp.sum() \
        if not p.empty else {}
    b["exp_next2"] = b.player_id.map(later).fillna(0.0)
    b["exp_3r"] = (b.exp_round + b.exp_next2).round(1)          # this round + the two after it
    b["exp_smart"] = (b.exp_round + 0.5 * b.exp_next2).round(1)
    # the round right after: buy once, use twice
    nx = p[p["round"] == rnd + 1].drop_duplicates("player_id").set_index("player_id") if not p.empty else None
    if nx is None or nx.empty:
        b["exp_next1"], b["next1_opp"], b["next1_venue"], b["next1_turn"] = 0.0, None, None, np.nan
    else:
        b["exp_next1"] = b.player_id.map(nx.exp_fp).fillna(0.0)
        b["next1_opp"] = b.player_id.map(nx.opp)
        b["next1_venue"] = b.player_id.map(nx.venue)
        b["next1_turn"] = b.player_id.map(nx.turn)
    b["exp_2r"] = (b.exp_round + b.exp_next1).round(1)                     # this round + the next one
    b["keep_score"] = np.minimum(b.exp_round, b.exp_next1).round(1)        # good in BOTH rounds
    b["exp_2r_value"] = b.exp_2r / b.price
    return b


def board(season: int, as_of: int):
    return _board(season, as_of, _version())


def coaches(season: int, as_of: int):
    return _coaches(season, as_of, _version())


def name_map(b):
    from .labels import pname
    return dict(zip(b.player_id, b.player_name.map(pname) + " · " + b.team.fillna("") + " · " +
                    b.position.fillna("?") + " · " + b.price.map(lambda x: f"{x:.1f}" if x == x else "—")))


MY_TEAM = os.getenv("MY_FANTASY_TEAM", "Green Magic")     # selected by default in the team pickers


def my_team_index(options) -> int:
    """Position of my fantasy team in a list of team names (emoji and case ignored), else 0."""
    k = re.sub(r"[^0-9a-zα-ω]", "", MY_TEAM.lower())
    for i, o in enumerate(options):
        if k and k in re.sub(r"[^0-9a-zα-ω]", "", str(o).lower()):
            return i
    return 0
