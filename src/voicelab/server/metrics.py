"""Own-process metrics are intentionally not collected in this baseline.

The helper feature is deferred; required resource coverage remains incomplete.
A future Linux metrics adapter needs a separate reviewed design and activation.
This component owns no probes, processes, threads, tasks, cache or timing source.
"""
from __future__ import annotations

from collections.abc import Callable


UNITS = {"rss_bytes": "bytes", "open_sockets": "count", "open_files": "count"}
DISABLED_REASON = "own_process_metrics_disabled"


class OwnProcessSampler:
    """Keep the async diagnostics interface without starting OS collection."""

    def __init__(self, *, clock: Callable[[], float] | None = None,
                 clock_ns: Callable[[], int] | None = None) -> None:
        # Accepted only for caller compatibility; neither clock is used or owned.
        pass

    async def sample(self) -> dict[str, dict[str, object]]:
        """Return fresh unknown rows; no collection or timing is performed."""
        return {name: {"value": None, "observation": "unknown", "unit": unit,
                       "reason": DISABLED_REASON} for name, unit in UNITS.items()}

    async def aclose(self) -> None:
        """No resources are owned, including after concurrent or cancelled calls."""
        return
