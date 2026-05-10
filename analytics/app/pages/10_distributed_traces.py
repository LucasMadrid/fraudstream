"""
Distributed Traces Viewer — Streamlit page for visualizing OpenTelemetry traces.

Allows operators to:
1. Search traces by trace ID, service name, or operation name
2. View trace timeline with span hierarchy
3. Filter traces by time range, status, or service
4. Inspect span details including attributes and events
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests
import streamlit as st

# Configure page layout
st.set_page_config(
    page_title="Distributed Traces",
    page_icon="📈",
    layout="wide",
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration & Constants
# ============================================================================

JAEGER_QUERY_URL = "http://jaeger:16686"
QUERY_TIMEOUT = 30

# Default time window for trace queries
DEFAULT_LOOKBACK_HOURS = 1

# Services that should appear in traces
KNOWN_SERVICES = [
    "fraudstream-processing",
    "fraudstream-scoring",
    "fraudstream-ingestion",
]

# ============================================================================
# Page Title & Description
# ============================================================================

st.title("📈 Distributed Traces")
st.markdown(
    """
    Search and visualize OpenTelemetry distributed traces across the fraud detection pipeline.
    
    **Features:**
    - Search by trace ID or service name
    - View end-to-end request flows
    - Inspect span timing and attributes
    - Filter by time range and status
    """
)

# ============================================================================
# Helper Functions: Jaeger API
# ============================================================================


def check_jaeger_available() -> bool:
    """Check if Jaeger query service is available."""
    try:
        response = requests.get(
            f"{JAEGER_QUERY_URL}/api/services",
            timeout=5,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


@st.cache_data(ttl=30)
def get_services() -> list[str]:
    """Fetch list of services from Jaeger."""
    try:
        response = requests.get(
            f"{JAEGER_QUERY_URL}/api/services",
            timeout=QUERY_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])
    except requests.RequestException as e:
        logger.error("Failed to fetch services from Jaeger: %s", e)
        return []


@st.cache_data(ttl=10)
def search_traces(
    service: str | None = None,
    operation: str | None = None,
    lookback_hours: int = 1,
    limit: int = 100,
    tags: dict[str, str] | None = None,
) -> list[dict]:
    """Search for traces in Jaeger.

    Args:
        service: Service name to filter by
        operation: Operation name to filter by
        lookback_hours: How many hours back to search
        limit: Maximum number of traces to return
        tags: Additional tags to filter by

    Returns:
        List of trace summaries
    """
    try:
        end_time = int(datetime.now().timestamp() * 1_000_000)
        start_time = int((datetime.now() - timedelta(hours=lookback_hours)).timestamp() * 1_000_000)

        params: dict[str, Any] = {
            "start": start_time,
            "end": end_time,
            "limit": limit,
        }

        if service:
            params["service"] = service
        if operation:
            params["operation"] = operation
        if tags:
            params["tags"] = json.dumps(tags)

        response = requests.get(
            f"{JAEGER_QUERY_URL}/api/traces",
            params=params,
            timeout=QUERY_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        traces = data.get("data", [])
        return traces
    except requests.RequestException as e:
        logger.error("Failed to search traces: %s", e)
        return []


@st.cache_data(ttl=10)
def get_trace_by_id(trace_id: str) -> dict | None:
    """Fetch a specific trace by ID.

    Args:
        trace_id: The trace ID to fetch

    Returns:
        Trace data or None if not found
    """
    try:
        response = requests.get(
            f"{JAEGER_QUERY_URL}/api/traces/{trace_id}",
            timeout=QUERY_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        traces = data.get("data", [])
        return traces[0] if traces else None
    except requests.RequestException as e:
        logger.error("Failed to fetch trace %s: %s", trace_id, e)
        return None


def get_operations(service: str) -> list[str]:
    """Fetch list of operations for a service.

    Args:
        service: Service name

    Returns:
        List of operation names
    """
    try:
        response = requests.get(
            f"{JAEGER_QUERY_URL}/api/operations",
            params={"service": service},
            timeout=QUERY_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])
    except requests.RequestException as e:
        logger.error("Failed to fetch operations for %s: %s", service, e)
        return []


def format_duration_us(duration_us: int) -> str:
    """Format duration in microseconds to human readable string."""
    if duration_us < 1000:
        return f"{duration_us}μs"
    elif duration_us < 1_000_000:
        return f"{duration_us / 1000:.2f}ms"
    else:
        return f"{duration_us / 1_000_000:.2f}s"


def flatten_spans(spans: list[dict], parent_id: str | None = None, depth: int = 0) -> list[dict]:
    """Flatten span hierarchy for display.

    Args:
        spans: List of root spans
        parent_id: Parent span ID
        depth: Current depth in hierarchy

    Returns:
        Flattened list of spans with depth info
    """
    result = []
    for span in spans:
        span_with_depth = {
            **span,
            "depth": depth,
            "parent_id": parent_id,
        }
        result.append(span_with_depth)

        # Find child spans (spans that reference this one as parent)
        child_spans = [
            s
            for s in spans
            if any(
                ref.get("refType") == "CHILD_OF" and ref.get("spanID") == span.get("spanID")
                for ref in s.get("references", [])
            )
        ]
        if child_spans:
            result.extend(flatten_spans(child_spans, span.get("spanID"), depth + 1))

    return result


# ============================================================================
# Sidebar: Search Controls
# ============================================================================

st.sidebar.header("🔍 Search Traces")

# Check Jaeger availability
if not check_jaeger_available():
    st.sidebar.error("❌ Jaeger not available. Ensure the jaeger service is running.")
else:
    st.sidebar.success("✅ Jaeger connected")

# Service filter
available_services = get_services()
selected_service = st.sidebar.selectbox(
    "Service",
    options=["All Services"] + (available_services or KNOWN_SERVICES),
    index=0,
)

# Operation filter
selected_operation = None
if selected_service != "All Services":
    operations = get_operations(selected_service)
    if operations:
        selected_operation = st.sidebar.selectbox(
            "Operation",
            options=["All Operations"] + operations,
            index=0,
        )
        if selected_operation == "All Operations":
            selected_operation = None

# Time range
lookback_hours = st.sidebar.slider(
    "Lookback (hours)",
    min_value=1,
    max_value=24,
    value=DEFAULT_LOOKBACK_HOURS,
)

# Limit
limit = st.sidebar.slider(
    "Max Results",
    min_value=10,
    max_value=500,
    value=100,
    step=10,
)

# Trace ID search
trace_id_search = st.sidebar.text_input(
    "Search by Trace ID",
    placeholder="Enter trace ID...",
)

# Status filter
status_filter = st.sidebar.multiselect(
    "Status Filter",
    options=["ok", "error"],
    default=["ok", "error"],
)

# Search button
search_clicked = st.sidebar.button("🔍 Search Traces", use_container_width=True)

# ============================================================================
# Main Content: Trace Display
# ============================================================================

if trace_id_search:
    # Single trace view
    st.header(f"Trace: {trace_id_search}")

    trace = get_trace_by_id(trace_id_search)
    if trace:
        spans = trace.get("spans", [])

        # Trace summary
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Spans", len(spans))
        with col2:
            services_in_trace = set(s.get("processID") for s in spans)
            st.metric("Services", len(services_in_trace))
        with col3:
            total_duration = max(
                (s.get("startTime", 0) + s.get("duration", 0) for s in spans),
                default=0,
            ) - min((s.get("startTime", 0) for s in spans), default=0)
            st.metric("Total Duration", format_duration_us(total_duration))
        with col4:
            error_count = sum(1 for s in spans if s.get("tags", [{}])[0].get("key") == "error")
            st.metric("Errors", error_count)

        # Trace timeline
        st.subheader("📈 Timeline")

        # Build span data for display
        span_data = []
        for span in spans:
            process_id = span.get("processID")
            process = trace.get("processes", {}).get(process_id, {})
            service_name = process.get("serviceName", "unknown")
            operation = span.get("operationName", "unknown")

            # Get span status
            tags = {t.get("key"): t.get("value") for t in span.get("tags", [])}
            status = "error" if tags.get("error") else "ok"

            span_data.append(
                {
                    "Service": service_name,
                    "Operation": operation,
                    "Duration": format_duration_us(span.get("duration", 0)),
                    "Duration (μs)": span.get("duration", 0),
                    "Status": status,
                    "Span ID": span.get("spanID"),
                    "Start Time": datetime.fromtimestamp(
                        span.get("startTime", 0) / 1_000_000
                    ).strftime("%H:%M:%S.%f")[:-3],
                }
            )

        span_df = pd.DataFrame(span_data)

        # Filter by status
        if status_filter:
            span_df = span_df[span_df["Status"].isin(status_filter)]

        # Display spans
        st.dataframe(
            span_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Duration (μs)": None,  # Hide raw duration column
            },
        )

        # Span details expander
        st.subheader("🔍 Span Details")
        selected_span_id = st.selectbox(
            "Select Span",
            options=[s.get("spanID") for s in spans],
            format_func=lambda x: next(
                (f"{s['operationName']} ({x})" for s in spans if s.get("spanID") == x),
                x,
            ),
        )

        if selected_span_id:
            selected_span = next(
                (s for s in spans if s.get("spanID") == selected_span_id),
                None,
            )
            if selected_span:
                with st.expander("Span Attributes", expanded=True):
                    tags = selected_span.get("tags", [])
                    if tags:
                        tag_df = pd.DataFrame(
                            [{"Key": t.get("key"), "Value": str(t.get("value"))} for t in tags]
                        )
                        st.dataframe(tag_df, use_container_width=True, hide_index=True)
                    else:
                        st.info("No attributes")

                with st.expander("Logs & Events"):
                    logs = selected_span.get("logs", [])
                    if logs:
                        for log in logs:
                            timestamp = datetime.fromtimestamp(
                                log.get("timestamp", 0) / 1_000_000
                            ).strftime("%H:%M:%S.%f")[:-3]
                            fields = {f.get("key"): f.get("value") for f in log.get("fields", [])}
                            st.text(f"{timestamp}: {fields}")
                    else:
                        st.info("No events")
    else:
        st.error(f"Trace {trace_id_search} not found")

elif search_clicked or not trace_id_search:
    # Trace list view
    st.header("📋 Recent Traces")

    service_filter = selected_service if selected_service != "All Services" else None

    traces = search_traces(
        service=service_filter,
        operation=selected_operation,
        lookback_hours=lookback_hours,
        limit=limit,
    )

    if traces:
        # Build trace summary table
        trace_data = []
        for trace in traces:
            spans = trace.get("spans", [])
            if not spans:
                continue

            # Get root span
            root_spans = [s for s in spans if not s.get("references")]
            root_span = root_spans[0] if root_spans else spans[0]

            # Calculate trace duration
            start_times = [s.get("startTime", 0) for s in spans]
            end_times = [s.get("startTime", 0) + s.get("duration", 0) for s in spans]
            duration_us = max(end_times) - min(start_times) if start_times else 0

            # Count errors
            error_count = sum(
                1
                for s in spans
                if any(t.get("key") == "error" and t.get("value") for t in s.get("tags", []))
            )

            # Get services
            processes = trace.get("processes", {})
            services = set(
                processes.get(s.get("processID"), {}).get("serviceName", "unknown") for s in spans
            )

            trace_data.append(
                {
                    "Trace ID": trace.get("traceID"),
                    "Root Operation": root_span.get("operationName", "unknown"),
                    "Services": ", ".join(sorted(services)),
                    "Spans": len(spans),
                    "Duration": format_duration_us(duration_us),
                    "Errors": error_count,
                    "Timestamp": datetime.fromtimestamp(min(start_times) / 1_000_000).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
            )

        trace_df = pd.DataFrame(trace_data)

        # Filter by status (if we have error counts)
        if "error" not in status_filter:
            trace_df = trace_df[trace_df["Errors"] == 0]
        elif "ok" not in status_filter:
            trace_df = trace_df[trace_df["Errors"] > 0]

        st.dataframe(
            trace_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Trace ID": st.column_config.TextColumn("Trace ID", width="medium"),
            },
        )

        st.info(f"Found {len(trace_df)} traces")
    else:
        st.info("No traces found matching the criteria")

# ============================================================================
# Help Section
# ============================================================================

with st.expander("❓ Help"):
    st.markdown(
        """
        ### Getting Started
        
        1. **Search Traces**: Select a service and time range, then click "Search Traces"
        2. **View Details**: Click on a Trace ID to see the full trace with all spans
        3. **Filter**: Use the status filter to show only successful or failed traces
        
        ### Understanding Traces
        
        - **Trace**: A complete request flow through the system
        - **Span**: A single operation within a trace
        - **Service**: The component that performed the operation
        - **Duration**: How long the operation took
        
        ### Common Issues
        
        - **No traces found**: Check that services are exporting to Jaeger
          (OTEL_EXPORTER_OTLP_ENDPOINT)
        - **Missing spans**: Verify the tracing instrumentation is active in the service
        - **Clock skew**: Spans may appear out of order if system clocks are not synchronized
        """
    )

# ============================================================================
# Footer
# ============================================================================

st.divider()
st.caption(
    "📊 Traces are collected via OpenTelemetry and stored in Jaeger. "
    "Ensure OTEL_EXPORTER_OTLP_ENDPOINT is set to http://jaeger:4318 in service environments."
)
