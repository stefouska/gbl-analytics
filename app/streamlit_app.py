"""Stoiximan GBL analytics — entry point.   streamlit run app/streamlit_app.py"""
import os
from pathlib import Path

os.environ["LEAGUE"] = "GBL"            # the shared pages hide the fantasy parts

import streamlit as st  # noqa: E402

st.set_page_config(page_title="GBL Analytics", page_icon="🇬🇷", layout="wide")

WH = Path(__file__).resolve().parents[1] / "data" / "warehouse.duckdb"
if not WH.exists():                     # no data yet: explain instead of crashing
    st.title("Stoiximan GBL")
    st.info("No data yet. The data arrive with the daily refresh (GitHub → Actions → daily-refresh → Run workflow); "
            "when it has finished, reboot the app.")
    st.stop()

pages = {
    "Stoiximan GBL": [
        st.Page("pages/dashboard.py", title="Dashboard", icon="🏠", default=True),
        st.Page("pages/players.py", title="Players", icon="👤"),
        st.Page("pages/teams.py", title="Teams", icon="🛡️"),
        st.Page("pages/compare.py", title="Compare players", icon="⚖️"),
        st.Page("pages/good_form.py", title="Good form", icon="📈"),
        st.Page("pages/bad_form.py", title="Bad form", icon="📉"),
        st.Page("pages/season_compare.py", title="Season comparison", icon="📊"),
        st.Page("pages/game_preview.py", title="Game preview", icon="🆚"),
    ],
}
st.navigation(pages, expanded=True).run()
