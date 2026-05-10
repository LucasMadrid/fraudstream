"""Shared Streamlit UI utilities for the analytics app."""

from __future__ import annotations

import logging
from collections.abc import Callable

import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)


def run_query(
    fn: Callable[[], pd.DataFrame],
    empty_msg: str = "No data for the selected period.",
) -> pd.DataFrame:
    """Execute a DataFrame query with standard error and empty-result handling.

    On exception: logs at ERROR level, shows st.error, returns empty DataFrame.
    On empty result: shows st.info(empty_msg), returns empty DataFrame.
    Caller checks ``if not df.empty`` before rendering.
    """
    try:
        df = fn()
    except Exception as exc:
        logger.exception("Query failed")
        st.error(f"Query failed: {exc}")
        return pd.DataFrame()
    if df.empty:
        st.info(empty_msg)
    return df
