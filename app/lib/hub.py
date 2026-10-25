"""Hub pages: one menu entry, several sub-pages chosen with buttons at the top. Only the chosen sub-page runs, so a
hub is as fast as the single page. The choice is kept in the URL (?view=…) and for the session."""
from pathlib import Path

import streamlit as st

PAGES = Path(__file__).resolve().parents[1] / "pages"


def run(key: str, views: dict[str, str]) -> None:
    """views: label -> file name in app/pages (e.g. {"Live scores": "live.py"})."""
    labels = list(views)
    slug = {lab: lab.split(" ", 1)[-1].lower().replace(" ", "-").replace("/", "") for lab in labels}
    wanted = st.query_params.get("view")
    start = next((lab for lab in labels if slug[lab] == wanted), None) or st.session_state.get(f"hub_{key}") or labels[0]
    choice = st.segmented_control("View", labels, default=start, key=f"hub_{key}", label_visibility="collapsed") \
        or start
    if st.query_params.get("view") != slug[choice]:
        st.query_params["view"] = slug[choice]
    path = PAGES / views[choice]
    code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
    exec(code, {"__name__": "__main__", "__file__": str(path)})
