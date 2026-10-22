"""Settings: API keys (with instructions) and rate limits per provider."""

from __future__ import annotations

import asyncio

import streamlit as st

from testbed import providers
from testbed.keys import key_source, load_keys, masked, remove_key, save_key
from testbed.providers.base import ProviderError
from testbed.settings import ProviderLimits, load_settings, save_settings
from testbed.ui.components.common import flash, home, show_flash


def test_key(provider: str, key: str) -> str | None:
    """None if the key works, else a short reason."""
    adapter = providers.adapter_class(provider)(key, timeout=20, max_retries=0)

    async def check():
        try:
            await adapter.test_key()
        finally:
            await adapter.aclose()

    try:
        asyncio.run(check())
    except ProviderError as error:
        return f"{error.kind} ({error.status_code}): {error.message[:200]}"
    except Exception as error:
        return f"{type(error).__name__}: {str(error)[:200]}"
    return None


def render() -> None:
    project = home()
    st.title("Settings")
    show_flash()
    st.subheader("API keys")
    st.markdown(
        "Add a key only for the providers you use; **one is enough**. Keys are saved in "
        f"`{project / '.env'}` (readable only by you) and are never written into scenarios, "
        "logs or results. Use a separate key with a spending limit for experiments."
    )
    keys = load_keys(project)
    for name in providers.provider_names():
        info = providers.provider_info(name)
        source = key_source(project, name)
        with st.container(border=True):
            left, right = st.columns([3, 2])
            with left:
                state = (f":green[set] ({masked(keys[name])}, from {source})"
                         if name in keys else ":gray[not set]")
                st.markdown(f"**{info.label}** · `{info.env_key}` · {state}")
                st.caption(f"Create a key at [{info.key_url}]({info.key_url}).")
            with right:
                if name in keys and st.button("Test key", key=f"test_{name}"):
                    with st.spinner("Calling the provider…"):
                        problem = test_key(name, keys[name])
                    if problem:
                        st.error(f"The key did not work: {problem}")
                    else:
                        st.success("The key works.")
            if source == "environment":
                st.info(f"This key comes from the `{info.env_key}` environment variable; "
                        "change it there.")
                continue
            with st.form(f"key_form_{name}", clear_on_submit=True, border=False):
                value = st.text_input(
                    f"New {info.label} key", type="password", key=f"key_input_{name}",
                    placeholder="Paste the key here",
                )
                save, remove = st.columns(2)
                saved = save.form_submit_button("Save key", width="stretch")
                removed = remove.form_submit_button(
                    "Remove key", width="stretch", disabled=name not in keys)
            if saved:
                try:
                    save_key(project, name, value)
                except ValueError as error:
                    st.error(str(error))
                else:
                    flash(f"{info.label} key saved.")
                    st.rerun()
            if removed:
                remove_key(project, name)
                flash(f"{info.label} key removed.", "info")
                st.rerun()

    st.subheader("Rate limits")
    st.markdown(
        "Set your account's limits (each provider's console shows them). They apply to the whole "
        "account, so running on more machines does not raise them. Leave a field at 0 for no limit. "
        "When a provider answers *429 Too Many Requests*, the testbed also slows down by itself."
    )
    settings = load_settings(project)
    with st.form("rate_limits"):
        updated = {}
        for name in providers.provider_names():
            limits = settings.limits(name)
            st.markdown(f"**{providers.provider_info(name).label}**")
            a, b, c = st.columns(3)
            rpm = a.number_input("Requests per minute", min_value=0, step=10,
                                 value=int(limits.requests_per_minute or 0), key=f"rpm_{name}")
            tpm = b.number_input("Input tokens per minute", min_value=0, step=10_000,
                                 value=int(limits.input_tokens_per_minute or 0), key=f"tpm_{name}")
            concurrent = c.number_input("Requests in flight at once", min_value=1, step=10,
                                        value=int(limits.max_concurrent), key=f"conc_{name}")
            updated[name] = ProviderLimits(
                requests_per_minute=rpm or None, input_tokens_per_minute=tpm or None,
                max_concurrent=concurrent,
            )
        if st.form_submit_button("Save rate limits"):
            settings.rate_limits.update(updated)
            save_settings(project, settings)
            flash("Rate limits saved.")
            st.rerun()
