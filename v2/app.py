"""Entry point: `streamlit run app.py`.

Serves the pages in ui.py plus the /api/agent/* routes the local connector uses.
"""

import streamlit as st

import api
import state

app = st.App("ui.py", routes=api.make_routes(state.get_community))
