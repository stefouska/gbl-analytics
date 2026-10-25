"""Lineup optimizer for the EuroLeague Fantasy Challenge (integer programming, scipy/HiGHS).

Official rules (fantaking.gitbook.io/euroleague-fantasy-challenge-rules):
  100 credits for 10 players (4 G, 4 F, 2 C) + 1 head coach · max 6 players from one team ·
  starting five in formation 2-2-1, 1-2-2, 2-1-2, 1-3-1 or 3-1-1 · bench players score 50% ·
  up to 4 trades per round (coach included), unlimited in some windows.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp

START_MIN = {"G": 1, "F": 1, "C": 1}
START_MAX = {"G": 3, "F": 3, "C": 2}


@dataclass
class Rules:
    squad_size: int = 10
    budget: float = 100.0
    coach_reserve: float = 0.0                           # credits kept for the head coach
    pos_min: dict = field(default_factory=lambda: {"G": 4, "F": 4, "C": 2})
    pos_max: dict = field(default_factory=lambda: {"G": 4, "F": 4, "C": 2})
    max_per_team: int = 6
    bench_weight: float = 0.5                            # bench players score 50%
    locked: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    current_team: list = field(default_factory=list)
    max_transfers: int | None = None
    max_per_team_pos: int | None = None                  # e.g. 1 = never two guards of the same team
    forbid_in_sets: list = field(default_factory=list)   # transfer plans already shown (sets of incoming ids)


def optimize(pool: pd.DataFrame, objective: str, rules: Rules, start_objective: str | None = None
             ) -> tuple[pd.DataFrame, str]:
    """Chooses 10 players (x) and a starting five (s ≤ x). Value = bench_weight·x + (1 − bench_weight)·s,
    i.e. starters count 100%, bench 50%. The squad is valued with `objective`; the starting five can use a
    different one (e.g. squad by 'this round + next games', starters by 'this round' only).
    Returns the squad with a 'starter' column."""
    start_objective = start_objective or objective
    p = pool.dropna(subset=["price", "position", objective, start_objective]).copy()
    p = p[p.position.isin(["G", "F", "C"]) & ~p.player_id.isin(rules.excluded)].reset_index(drop=True)
    n = len(p)
    if n == 0:
        return p, "No players available."
    v = p[objective].to_numpy(float)
    vs = p[start_objective].to_numpy(float)
    # variables: x_0..x_{n-1} (in squad), s_0..s_{n-1} (starter)
    c = -np.concatenate([rules.bench_weight * v, (1 - rules.bench_weight) * vs])
    A, lo, hi = [], [], []

    def add(xrow=None, srow=None, low=-np.inf, high=np.inf):
        A.append(np.concatenate([xrow if xrow is not None else np.zeros(n),
                                 srow if srow is not None else np.zeros(n)]))
        lo.append(low); hi.append(high)

    one = np.ones(n)
    add(one, None, rules.squad_size, rules.squad_size)
    add(p.price.to_numpy(float), None, 0, rules.budget - rules.coach_reserve)
    for pos in ("G", "F", "C"):
        m = (p.position == pos).to_numpy(float)
        add(m, None, rules.pos_min.get(pos, 0), rules.pos_max.get(pos, rules.squad_size))
        add(None, m, START_MIN[pos], START_MAX[pos])
    add(None, one, 5, 5)
    for club in p.club_code.dropna().unique():
        add((p.club_code == club).to_numpy(float), None, 0, rules.max_per_team)
        if rules.max_per_team_pos:
            for pos in ("G", "F", "C"):
                m = ((p.club_code == club) & (p.position == pos)).to_numpy(float)
                if m.sum() > rules.max_per_team_pos:
                    add(m, None, 0, rules.max_per_team_pos)
    for i in range(n):                                   # starter must be in the squad: s_i − x_i ≤ 0
        xr, sr = np.zeros(n), np.zeros(n)
        xr[i], sr[i] = -1, 1
        add(xr, sr, -np.inf, 0)
    if rules.current_team and rules.max_transfers is not None:
        keep = p.player_id.isin(rules.current_team).to_numpy(float)
        in_pool = int(keep.sum())
        add(keep, None, max(0, in_pool - rules.max_transfers), rules.squad_size)
    for ins in rules.forbid_in_sets:                     # a different plan than the ones already shown
        ins = set(ins)
        if ins:
            add(p.player_id.isin(ins).to_numpy(float), None, -np.inf, len(ins) - 1)
        elif rules.current_team:                         # 'keep your team' was shown: at least one transfer
            keep = p.player_id.isin(rules.current_team).to_numpy(float)
            add(keep, None, -np.inf, keep.sum() - 1)

    lb = np.concatenate([p.player_id.isin(rules.locked).to_numpy(float), np.zeros(n)])
    res = milp(c, integrality=np.ones(2 * n), bounds=Bounds(lb, np.ones(2 * n)),
               constraints=LinearConstraint(np.array(A), lo, hi))
    if not res.success:
        return p.iloc[0:0], ("No squad fits these rules — loosen the budget, position limits, "
                             "players per team or locked players.")
    x, s = res.x[:n] > 0.5, res.x[n:] > 0.5
    squad = p[x].assign(starter=s[x]).sort_values(["starter", start_objective], ascending=[False, False])
    return squad, "ok"


def replacement_ideas(board: pd.DataFrame, current: list, objective: str, bank: float,
                      per_player: int = 3) -> pd.DataFrame:
    """For every player of the current team: the best affordable same-position alternatives
    (price ≤ his price + bank) that are not already in the team, with the gain in the objective."""
    b = board.dropna(subset=["price", objective])
    mine = b[b.player_id.isin(current)]
    rows = []
    for r in mine.itertuples():
        alt = b[(b.position == r.position) & ~b.player_id.isin(current) & (b.price <= r.price + bank)
                & ~b.unavailable]
        alt = alt.assign(gain=alt[objective] - getattr(r, objective)).query("gain > 0").nlargest(per_player, "gain")
        for a in alt.itertuples():
            rows.append({"out": r.player_name, "out_price": r.price, "out_value": getattr(r, objective),
                         "in": a.player_name, "in_team": a.team, "in_price": a.price,
                         "in_value": getattr(a, objective), "gain": a.gain, "cost": a.price - r.price,
                         "out_id": r.player_id, "in_id": a.player_id})
    return pd.DataFrame(rows).sort_values("gain", ascending=False) if rows else pd.DataFrame()


def optimize_with_coach(pool: pd.DataFrame, objective: str, rules: Rules, coaches: pd.DataFrame,
                        start_objective: str | None = None, current_coach: str | None = None):
    """Players + head coach in the same budget. coaches: club_code, coach, price, value (expected coach points on
    the same horizon as `objective`). Each coach is tried (dominated ones skipped: dearer AND worse than another);
    changing the coach uses one of the transfers. Returns (squad, coach row or None, message)."""
    c = coaches.dropna(subset=["price", "value"]).sort_values(["price", "value"], ascending=[True, False])
    keep, best_v = [], -np.inf                       # Pareto front: cheaper or better than every cheaper one
    for r in c.itertuples():
        if r.value > best_v + 1e-9 or r.club_code == current_coach:
            keep.append(r.Index)
            best_v = max(best_v, r.value)
    c = c.loc[keep]
    best = (None, None, "No coach fits the budget.")
    best_total = -np.inf
    cur_price = float(coaches.set_index("club_code").price.get(current_coach, 0)) if current_coach else 0.0
    for r in c.itertuples():
        change = current_coach is not None and r.club_code != current_coach
        mt = rules.max_transfers
        if change and mt is not None:
            if mt < 1:
                continue
            mt = mt - 1                                  # the coach counts as one of the transfers
        budget = rules.budget + (cur_price if current_coach else 0.0) - r.price
        rr = Rules(**{**rules.__dict__, "budget": budget, "coach_reserve": 0.0, "max_transfers": mt})
        squad, msg = optimize(pool, objective, rr, start_objective)
        if msg != "ok":
            continue
        w = np.where(squad.starter, 1.0, rules.bench_weight)
        total = float((squad[objective].fillna(0) * w).sum()) + float(r.value)
        if total > best_total:
            best_total = total
            best = (squad, coaches.loc[r.Index], "ok")
    return best


# ---------------------------------------------------------------- smart transfer recommendations
def transfer_risk(p) -> tuple[str, list[str]]:
    """Risk of buying player p (a board row): 🟢 low / 🟠 medium / 🔴 high, with the reasons."""
    why, pts = [], 0
    flag = str(p.get("inj_flag") or "OK")
    if flag == "Q":
        why.append("doubtful"); pts += 2
    elif flag == "OUT":
        why.append("injured"); pts += 4
    gp = p.get("gp", 0) or 0
    if gp < 3:
        why.append(f"only {int(gp)} game(s) — small sample"); pts += 1
    rs = p.get("risk_score")
    if rs == rs and rs is not None and rs < 35:
        why.append("unstable role / minutes"); pts += 2
    bad = p.get("bad_score")
    if bad == bad and bad is not None and bad >= 70:
        why.append("bad form"); pts += 2
    mv = p.get("exp_round_move")
    if mv == mv and mv is not None and mv < -0.2:
        why.append(f"price likely to fall ({mv:+.1f})"); pts += 1
    pr = p.get("price_up_prob")
    if pr == pr and pr is not None and pr >= 0.6:
        why.append(f"price likely to rise ({pr:.0%})")
    level = "🔴 high" if pts >= 4 else ("🟠 medium" if pts >= 2 else "🟢 low")
    return level, why


def explain_transfers(outs: pd.DataFrame, ins: pd.DataFrame, R: int) -> pd.DataFrame:
    """Pair every player sold with a player bought (same position first, then by expected points) and explain
    each move: expected FP gained over the next rounds, extra cost, risk, and a verdict."""
    outs = outs.sort_values("exp_3r", ascending=False).copy()
    ins = ins.sort_values("exp_3r", ascending=False).copy()
    pairs, used = [], set()
    for _, o in outs.iterrows():                       # same position first
        cand = ins[(ins.position == o.position) & ~ins.player_id.isin(used)]
        if cand.empty:
            cand = ins[~ins.player_id.isin(used)]
        if cand.empty:
            break
        i = cand.iloc[0]
        used.add(i.player_id)
        pairs.append((o, i))
    rows = []
    for o, i in pairs:
        g1 = (i.get("exp_round", 0) or 0) - (o.get("exp_round", 0) or 0)
        g2 = (i.get("exp_next1", 0) or 0) - (o.get("exp_next1", 0) or 0)
        g3 = (i.get("exp_3r", 0) or 0) - (o.get("exp_3r", 0) or 0)
        cost = (i.price or 0) - (o.price or 0)
        risk, why = transfer_risk(i)
        out_reasons = []
        if str(o.get("inj_flag") or "OK") != "OK":
            out_reasons.append("injured/doubtful")
        if (o.get("bad_score") or 0) >= 70:
            out_reasons.append("bad form")
        if (o.get("exp_round", 0) or 0) == 0:
            out_reasons.append(f"no game in R{R}")
        if (o.get("exp_round_move") or 0) < -0.2:
            out_reasons.append("price falling")
        if g3 <= 0 and cost < -1:
            verdict = "💰 Frees credits"
        elif g3 >= 6 and not risk.startswith("🔴"):
            verdict = "✅ Do it"
        elif g3 >= 2.5 and not risk.startswith("🔴"):
            verdict = "👍 Worth it"
        elif g3 > 0:
            verdict = "🤔 Marginal"
        else:
            verdict = "❌ Keep him"
        txt = (f"Out **{o.player_name}**, in **{i.player_name}**: **{g3:+.1f}** expected FP over rounds {R}-{R + 2} "
               f"({g1:+.1f} in R{R}), {'+' if cost >= 0 else '−'}{abs(cost):.1f} credits")
        if verdict == "💰 Frees credits":
            txt += f" — a cheaper player that frees **{abs(cost):.1f} credits** for the other moves"
        rows.append({"out": o.player_name, "out_id": o.player_id, "in": i.player_name, "in_id": i.player_id,
                     "pos": i.position, "gain_r": g1, "gain_r1": g2, "gain_3r": g3, "cost": cost, "risk": risk,
                     "risk_why": ", ".join(why), "sell_why": ", ".join(out_reasons), "verdict": verdict, "text": txt})
    return pd.DataFrame(rows)


def keep_or_sell(team: pd.DataFrame, pool: pd.DataFrame, budget_left: float, R: int) -> pd.DataFrame:
    """For each player of the team: the best affordable replacement at his position (next 3 rounds) and
    whether it is worth keeping him."""
    rows = []
    for _, p in team.iterrows():
        alt = pool[(pool.position == p.position) & ~pool.player_id.isin(team.player_id)
                   & (pool.price <= (p.price or 0) + budget_left) & ~pool.unavailable]
        best = alt.nlargest(1, "exp_3r")
        gain = float(best.exp_3r.iloc[0] - (p.exp_3r or 0)) if len(best) else 0.0
        reasons = []
        if str(p.get("inj_flag") or "OK") != "OK":
            reasons.append("injured/doubtful")
        if (p.get("bad_score") or 0) >= 70:
            reasons.append("bad form")
        if (p.get("good_score") or 0) >= 80:
            reasons.append("🔥 hot")
        pr = p.get("price_up_prob")
        if pr == pr and pr is not None:
            reasons.append(f"P(price ▲) {pr:.0%}")
        if gain >= 6 or str(p.get("inj_flag") or "OK") == "OUT":
            verdict = "🔴 Sell"
        elif gain >= 2.5:
            verdict = "🟠 Consider selling"
        else:
            verdict = "🟢 Keep"
        rows.append({"player": p.player_name, "pos": p.position, "price": p.price, "exp_3r": p.exp_3r,
                     "best_alt": best.player_name.iloc[0] if len(best) else "—",
                     "alt_exp_3r": best.exp_3r.iloc[0] if len(best) else None,
                     "alt_price": best.price.iloc[0] if len(best) else None, "gain": gain,
                     "notes": ", ".join(reasons), "verdict": verdict})
    return pd.DataFrame(rows).sort_values("gain", ascending=False)
