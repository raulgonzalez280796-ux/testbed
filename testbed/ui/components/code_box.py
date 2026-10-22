"""A Python code editor with syntax highlighting, falling back to a plain text box.

The highlighted editor is a third-party component (streamlit-code-editor). If it
is missing or misbehaves, the "Plain text editor" switch in the sidebar uses
st.text_area instead; both return the current text.
"""

from __future__ import annotations

import streamlit as st

try:
    from code_editor import code_editor
except Exception:  # not installed, or incompatible with this Streamlit
    code_editor = None


def plain_mode() -> bool:
    return code_editor is None or st.session_state.get("plain_editor", False)


def code_box(key: str, value: str, height: int = 22) -> str:
    """Show an editor for `value`; returns the text as currently edited."""
    if plain_mode():
        return st.text_area("Code", value, height=height * 20, key=f"{key}_plain",
                            label_visibility="collapsed")
    response = code_editor(
        value, lang="python", height=[height, height + 10], key=f"{key}_editor",
        response_mode="debounce", options={"showLineNumbers": True, "tabSize": 4},
    )
    if response and response.get("id"):  # an edit has been reported back
        st.session_state[f"{key}_last"] = response.get("text", "")
    return st.session_state.get(f"{key}_last", value)
