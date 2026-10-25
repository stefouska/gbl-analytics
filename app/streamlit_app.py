"""Stoiximan GBL analytics — entry point.   streamlit run app/streamlit_app.py"""
import os

os.environ["LEAGUE"] = "GBL"            # the shared pages hide the fantasy parts

import streamlit as st  # noqa: E402

st.set_page_config(page_title="GBL Analytics", page_icon="🇬🇷", layout="wide")
pages = {
    "Stoiximan GBL": [
        st.Page("pages/dashboard.py", title="Dashboard", icon="🏠", default=True),
        st.Page("pages/players.py", title="Players", icon="👤"),
        st.Page("pages/teams.py", title="Teams", icon="🛡️"),
        st.Page("pages/season_compare.py", title="Season comparison", icon="📊"),
        st.Page("pages/game_preview.py", title="Game preview", icon="🆚"),
    ],
}
st.navigation(pages, expanded=True).run()
