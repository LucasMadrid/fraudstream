"""Safe metric wrappers for PyFlink workers where prometheus_client may be absent.

Each wrapper delegates to the real prometheus_client object when available and
falls back to silent no-ops otherwise.  This satisfies Principle VIII (observability
is first-class, silent failures are forbidden) by ensuring metric call-sites never
crash, while still emitting real metrics when the library is present.
"""

from __future__ import annotations

try:
    import prometheus_client as _prom

    _HAS_PROMETHEUS = True
except ImportError:  # pragma: no cover – tested via mock
    _prom = None  # type: ignore[assignment]
    _HAS_PROMETHEUS = False


# ---------------------------------------------------------------------------
# No-op label child (returned by SafeCounter.labels / SafeGauge.labels)
# ---------------------------------------------------------------------------

class _NoOpChild:
    """Placeholder returned by no-op .labels() calls."""

    def inc(self, amount: float = 1) -> None:  # noqa: ARG002
        pass

    def set(self, value: float) -> None:  # noqa: ARG002, A003
        pass

    def observe(self, amount: float) -> None:  # noqa: ARG002
        pass


_NOOP_CHILD = _NoOpChild()


# ---------------------------------------------------------------------------
# SafeCounter
# ---------------------------------------------------------------------------

class SafeCounter:
    """Drop-in replacement for ``prometheus_client.Counter``.

    If *prometheus_client* is available the real ``Counter`` is created and all
    operations delegate to it.  Otherwise every method is a no-op.
    """

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: list[str] | tuple[str, ...] = (),
    ) -> None:
        self._inner = (
            _prom.Counter(name, documentation, labelnames)
            if _HAS_PROMETHEUS
            else None
        )

    # -- public API used in the scoring pipeline --

    def labels(self, *args, **kwargs):  # noqa: ANN002,ANN003,ANN201
        if self._inner is not None:
            return self._inner.labels(*args, **kwargs)
        return _NOOP_CHILD

    def inc(self, amount: float = 1) -> None:
        if self._inner is not None:
            self._inner.inc(amount)

    # Expose _labelnames so existing tests that inspect it still pass.
    @property
    def _labelnames(self):  # noqa: ANN202
        if self._inner is not None:
            return self._inner._labelnames
        return ()


# ---------------------------------------------------------------------------
# SafeGauge
# ---------------------------------------------------------------------------

class SafeGauge:
    """Drop-in replacement for ``prometheus_client.Gauge``."""

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: list[str] | tuple[str, ...] = (),
    ) -> None:
        self._inner = (
            _prom.Gauge(name, documentation, labelnames)
            if _HAS_PROMETHEUS
            else None
        )

    def labels(self, *args, **kwargs):  # noqa: ANN002,ANN003,ANN201
        if self._inner is not None:
            return self._inner.labels(*args, **kwargs)
        return _NOOP_CHILD

    def set(self, value: float) -> None:  # noqa: A003
        if self._inner is not None:
            self._inner.set(value)

    @property
    def _labelnames(self):  # noqa: ANN202
        if self._inner is not None:
            return self._inner._labelnames
        return ()


# ---------------------------------------------------------------------------
# SafeHistogram
# ---------------------------------------------------------------------------

class SafeHistogram:
    """Drop-in replacement for ``prometheus_client.Histogram``."""

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: list[str] | tuple[str, ...] = (),
        buckets: list[float] | tuple[float, ...] = (),
    ) -> None:
        kwargs: dict = {}
        if buckets:
            kwargs["buckets"] = buckets
        self._inner = (
            _prom.Histogram(name, documentation, labelnames, **kwargs)
            if _HAS_PROMETHEUS
            else None
        )

    def labels(self, *args, **kwargs):  # noqa: ANN002,ANN003,ANN201
        if self._inner is not None:
            return self._inner.labels(*args, **kwargs)
        return _NOOP_CHILD

    def observe(self, amount: float) -> None:
        if self._inner is not None:
            self._inner.observe(amount)

    @property
    def _labelnames(self):  # noqa: ANN202
        if self._inner is not None:
            return self._inner._labelnames
        return ()
