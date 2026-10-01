"""Trial metrics beyond the simulator's own summary: the per-window max.

Max is one passenger in four hundred, so the trials pair it with steadier companions. Every
statistic counts every passenger in the run; nothing is trimmed from its start.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import Passenger


def bucketed_max(passengers: Sequence[Passenger], window: int) -> list[tuple[int, int | None]]:
    """Max total time per window, keyed by the window's first tick.

    Windows are cut on **request** time, so a bucket says what the passengers arriving in
    that window went on to suffer. Flat across windows means the fleet keeps up; climbing
    means it never does.

    Every experiment run drains, so every passenger has a total time; one who somehow has none is
    left out, and a window with nobody scored is ``None``.

    Args:
        passengers: The passengers to bucket.
        window: Width of each window in ticks.

    Returns:
        ``(window start tick, max total time in ticks)`` per window that had any arrivals,
        in time order. The max is ``None`` when nobody in the window could be scored.

    Raises:
        ValueError: ``window`` is less than 1.
    """
    if window < 1:
        raise ValueError("window must be >= 1 tick")
    buckets: dict[int, list[int]] = {}
    for p in passengers:
        # Round the request time down to the start of its window.
        start = p.request.time // window * window
        totals = buckets.setdefault(start, [])
        if p.total_time is not None:
            totals.append(p.total_time)
    return [(start, max(totals) if totals else None) for start, totals in sorted(buckets.items())]

