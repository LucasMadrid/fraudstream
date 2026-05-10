"""Null-safe Prometheus metric proxy shared across all pipeline packages."""

from __future__ import annotations


class _SafeMetric:
    """Proxy that makes any Prometheus metric call guaranteed to not raise.

    Wraps .inc(), .observe(), .set(), .labels() — and any result they return —
    so callers never need a try/except guard. Failure silently returns a null proxy.
    """

    __slots__ = ("_m",)

    def __init__(self, metric) -> None:
        object.__setattr__(self, "_m", metric)

    def __getattr__(self, name: str):
        m = object.__getattribute__(self, "_m")
        if m is None:
            return lambda *a, **kw: _SafeMetric(None)
        attr = getattr(m, name, None)
        if attr is None:
            return lambda *a, **kw: _SafeMetric(None)
        if not callable(attr):
            return attr

        def _call(*args, **kwargs):
            try:
                return _SafeMetric(attr(*args, **kwargs))
            except Exception:
                return _SafeMetric(None)

        return _call
