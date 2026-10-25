"""Shared UI: sidebar filters, formatting, charts."""
import pandas as pd
import plotly.express as px
import streamlit as st

from . import db
from .labels import pname, arrow, arrow_color  # noqa: F401  (re-exported for the pages)
from .metrics import PLAYER, TEAM

ACCENT = "#16a34a"
px.defaults.template = "simple_white"
px.defaults.color_discrete_sequence = ["#111827", ACCENT, "#9ca3af", "#f59e0b", "#2563eb", "#dc2626"]

PHASES = {"RS": "Regular Season", "PI": "Play-ins", "PO": "Play-offs", "FF": "Final Four"}


@st.cache_data(ttl=3600)
def _seasons():
    return db.seasons()


@st.cache_data(ttl=3600)
def _rounds(season):
    return db.rounds(season)


@st.cache_data(ttl=3600)
def _dim(sql):
    return db.q(sql)


def sidebar_filters(key: str, with_player_attrs=False) -> tuple[db.Filters, dict]:
    """Game filters (season, phase, rounds, venue, team, country) + optional player filters."""
    sb = st.sidebar
    seasons = _seasons()
    if not seasons:
        st.warning("No data yet — the daily job has not run.")
        st.stop()
    season = sb.selectbox("Season", seasons, format_func=lambda s: f"{s}-{str(s + 1)[-2:]}", key=f"{key}_s")
    rnds = _rounds(season)
    lo, hi = (min(rnds), max(rnds)) if rnds else (1, 1)
    rng = sb.slider("Rounds", lo, max(hi, lo + 1), (lo, hi), key=f"{key}_r") if hi > lo else (lo, hi)
    teams = _dim("""SELECT club_code, tag, team_name, country FROM dim_team
                    WHERE club_code IN (SELECT DISTINCT club_code FROM fct_team_game) ORDER BY team_name""")
    clubs = sb.multiselect("Team", teams.club_code, format_func=dict(zip(teams.club_code, teams.team_name)).get,
                           key=f"{key}_t")
    venue = sb.radio("Location", ["All", "Home", "Away"], horizontal=True, key=f"{key}_v")
    with sb.expander("Season segment & country"):
        phases = st.multiselect("Season segment", list(PHASES), format_func=PHASES.get, key=f"{key}_ph")
        countries = st.multiselect("Team country", sorted(teams.country.dropna().unique()), key=f"{key}_c")
    f = db.Filters(season=season, phases=phases, rounds=tuple(rng), venue={"Home": "H", "Away": "A"}.get(venue),
                   clubs=clubs, countries=countries)
    extra = {}
    if with_player_attrs:
        players = _dim("SELECT nationality, position, height_cm, age FROM dim_player")
        extra["position"] = sb.multiselect("Position", ["G", "F", "C"], key=f"{key}_p")
        with sb.expander("More filters"):
            extra["nationality"] = st.multiselect("Nationality", sorted(players.nationality.dropna().unique()),
                                                  key=f"{key}_n")
            if players.height_cm.notna().any():
                h = players.height_cm.dropna()
                extra["height"] = st.slider("Height (cm)", int(h.min()), int(h.max()), (int(h.min()), int(h.max())),
                                            key=f"{key}_h")
            if players.age.notna().any():
                a = players.age.dropna()
                extra["age"] = st.slider("Age", int(a.min()), int(a.max()), (int(a.min()), int(a.max())), key=f"{key}_a")
    footer()
    return f, extra


def apply_player_attrs(df: pd.DataFrame, extra: dict) -> pd.DataFrame:
    if extra.get("nationality"):
        df = df[df.nationality.isin(extra["nationality"])]
    if extra.get("position"):
        df = df[df.position.isin(extra["position"])]
    if extra.get("height") and df.height_cm.notna().any():
        df = df[df.height_cm.between(*extra["height"]) | df.height_cm.isna()]
    if extra.get("age") and df.age.notna().any():
        df = df[df.age.between(*extra["age"]) | df.age.isna()]
    return df


def footer():
    s = db.status()
    if s:
        fails = [k for k, v in s.items() if str(v).startswith("FAILED")]
        run_at = str(s.get("_pregame_at") or s.get("_run_at", ""))
        try:
            when = pd.Timestamp(run_at).tz_convert("Europe/Athens").strftime("%d/%m %H:%M") if run_at else "?"
        except (ValueError, TypeError):
            when = "/".join(reversed(run_at[:10].split("-"))) if run_at else "?"
        st.sidebar.caption(f"Data refreshed: {when}"
                           + (f"  \n⚠️ Failed: {', '.join(fails)}" if fails else ""))
    refresh_button()


# ---------------------------------------------------------------- "Refresh data" (starts the GitHub workflow)
GH_API = "https://api.github.com"


def _gh():
    """(token, repo) from the Streamlit secrets GITHUB_TOKEN / GITHUB_REPO, or (None, None)."""
    try:
        tok = st.secrets.get("GITHUB_TOKEN")
        repo = st.secrets.get("GITHUB_REPO", "stefouska/gbl-analytics")
    except Exception:                                    # no secrets file at all
        return None, None
    return tok, repo


@st.cache_data(ttl=15, show_spinner=False)
def _last_run(repo: str, token: str) -> dict:
    import requests
    try:
        r = requests.get(f"{GH_API}/repos/{repo}/actions/workflows/daily.yml/runs", params={"per_page": 1},
                         headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                         timeout=10)
        runs = r.json().get("workflow_runs", []) if r.ok else []
        return runs[0] if runs else {}
    except Exception:
        return {}


def refresh_button():
    tok, repo = _gh()
    if not tok:
        return
    import requests
    run = _last_run(repo, tok)
    running = run.get("status") in ("queued", "in_progress", "waiting", "requested", "pending")
    box = st.sidebar.container()
    if running:
        started = pd.Timestamp(run.get("created_at")).tz_convert("Europe/Athens").strftime("%H:%M")
        box.caption(f"⏳ Refresh running (started {started}) — new data in a few minutes")
        if box.button("Check again", key="gh_check", width="stretch"):
            _last_run.clear()
            st.rerun()
        return
    if run.get("conclusion") == "failure":
        box.caption("⚠️ The last refresh failed — see GitHub → Actions")
    if box.button("🔄 Refresh data", key="gh_refresh", width="stretch",
                  help="Downloads the latest games, injuries, prices and fantasy results (≈ 3-5 minutes)"):
        try:
            r = requests.post(f"{GH_API}/repos/{repo}/actions/workflows/daily.yml/dispatches",
                              json={"ref": "main", "inputs": {"full": False}},
                              headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                              timeout=15)
        except Exception as e:
            st.toast(f"Could not reach GitHub: {e}")
            return
        if r.status_code == 204:
            st.toast("Refresh started ✅ — the new data appear in about 5 minutes")
            _last_run.clear()
        else:
            st.toast(f"Could not start the refresh (GitHub said {r.status_code}). Check the GITHUB_TOKEN secret.")


def column_config(keys, catalog=PLAYER):
    cfg = {}
    for k in keys:
        m = catalog.get(k)
        if not m:
            continue
        fmt = {"int": "%d", "dec1": "%.1f", "dec2": "%.2f", "pct": "%.1f%%"}.get(m.fmt)
        cfg[k] = st.column_config.NumberColumn(m.label, help=m.desc, format=fmt) if fmt \
            else st.column_config.TextColumn(m.label, help=m.desc)
    return cfg


def pct_to_100(df: pd.DataFrame, keys, catalog=PLAYER) -> pd.DataFrame:
    """Streamlit's % format expects 0-100."""
    df = df.copy()
    for k in keys:
        if k in df and catalog.get(k) and catalog[k].fmt == "pct":
            df[k] = df[k] * 100
    return df


ID_LABELS = {"player_name": "Player", "team": "Team", "team_name": "Team", "position": "Pos", "tag": "Team",
             "split": "", "nationality": "Nationality"}


def table(df: pd.DataFrame, keys, id_cols, catalog=PLAYER, height=420, **kw):
    show = pct_to_100(df, keys, catalog)[[*id_cols, *[k for k in keys if k in df]]]
    if "player_name" in show:
        show = show.assign(player_name=show.player_name.map(pname))
    cfg = {c: st.column_config.TextColumn(ID_LABELS.get(c, c)) for c in id_cols}
    cfg.update(column_config(keys, catalog))
    st.dataframe(show, column_config=cfg, hide_index=True, height=height, width="stretch", **kw)


def round_axis(fig, axis="x"):
    """Rounds are whole numbers: 1, 2, 3 — never 0.5 or 1.5."""
    upd = dict(tickmode="linear", dtick=1, tick0=1, tickformat="d", title_text="Round")
    (fig.update_xaxes if axis == "x" else fig.update_yaxes)(**upd)
    return fig


def header(title: str, subtitle: str = ""):
    st.markdown(f"## {title}")
    if subtitle:
        st.caption(subtitle)
