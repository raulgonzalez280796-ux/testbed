"""llmArena testbed UI. Start it with: python -m testbed ui

It only edits files in the project folder and starts runs as separate
processes; all logic lives in the testbed package, so the UI can be replaced.
"""

import streamlit as st

from testbed import __version__
from testbed.project import ensure_project
from testbed.ui.components.code_box import code_editor
from testbed.ui.components.common import home
from testbed.ui.pages import results, run, scenarios, settings, tools_page

st.set_page_config(page_title="llmArena testbed", page_icon=":material/hub:", layout="wide")

project = home()
ensure_project(project)

PAGES = {
    "scenarios": st.Page(scenarios.render, title="Scenarios", icon=":material/edit_note:",
                         url_path="scenarios", default=True),
    "tools": st.Page(tools_page.render, title="Tools & checks", icon=":material/build:", url_path="tools"),
    "run": st.Page(run.render, title="Run", icon=":material/play_arrow:", url_path="run"),
    "results": st.Page(results.render, title="Results", icon=":material/table_view:", url_path="results"),
    "settings": st.Page(settings.render, title="Settings", icon=":material/key:", url_path="settings"),
}
st.session_state["pages"] = PAGES

with st.sidebar:
    st.markdown(f"**llmArena testbed** {__version__}")
    st.caption(f"Project folder:\n\n`{project}`")
    if code_editor is not None:
        st.toggle("Plain text code editor", key="plain_editor",
                  help="Use a simple text box instead of the highlighted editor.")

st.navigation(list(PAGES.values())).run()
