"""Mid-round decisions: after Turn 1, the players who already played have known points and a fixed role.
For the players still to play, choose the starting five (formation rules) and the captain that maximise
   points of the round = Σ starters + ½ Σ bench + captain's points once more (captain ×2).
A player whose game has started can no longer be moved or made captain."""
from itertools import combinations

import pandas as pd

from .optimizer import START_MAX, START_MIN

BENCH = 0.5


def _formation_ok(pos: list[str]) -> bool:
    return len(pos) == 5 and all(START_MIN[p] <= pos.count(p) <= START_MAX[p] for p in ("G", "F", "C"))


def score(team: pd.DataFrame, starters: set, captain) -> float:
    v = team.set_index("player_id").value
    tot = sum(v[p] if p in starters else BENCH * v[p] for p in v.index)
    if captain in v.index and captain in starters:
        tot += v[captain]
    return float(tot)


def best_plan(team: pd.DataFrame) -> dict:
    """team: player_id, position, starter, captain, played, value (actual FP if played, else expected).
    Returns {'starters', 'captain', 'score', 'current_score'}."""
    t = team.copy()
    t["starter"] = t.starter.astype(bool)
    t["played"] = t.played.astype(bool)
    pos = t.set_index("player_id").position
    locked_start = set(t[t.played & t.starter].player_id)
    free = list(t[~t.played].player_id)
    cur_start = set(t[t.starter].player_id)
    cur_cap = t[t.captain.astype(bool)].player_id.iloc[0] if t.captain.astype(bool).any() else None
    current = score(t, cur_start, cur_cap)
    cap_locked = cur_cap is not None and bool(t.set_index("player_id").played.get(cur_cap, False))
    need = 5 - len(locked_start)
    best = {"starters": cur_start, "captain": cur_cap, "score": current}
    if need < 0:
        return {**best, "current_score": current}
    vals = t.set_index("player_id").value
    for combo in combinations(free, need):
        st_ = locked_start | set(combo)
        if not _formation_ok([pos[p] for p in st_]):
            continue
        if cap_locked:
            caps = [cur_cap]
        else:                                           # captain: any starter whose game has not started
            caps = [p for p in combo] or [cur_cap]
        for c in caps:
            sc = score(t, st_, c)
            if sc > best["score"] + 1e-9:
                best = {"starters": st_, "captain": c, "score": sc}
    return {**best, "current_score": current, "captain_locked": cap_locked}
