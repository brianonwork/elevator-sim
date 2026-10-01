"""Paired differences over common random numbers.

**Common random numbers** means every scheduler (each "arm" of the comparison) is run on exactly the
same random traffic for a given seed. We then take the difference between two arms seed by seed, and
ask whether the average difference is large compared with its **standard error** (how much that
average would wobble from sampling alone). That ratio is the **paired t** statistic. **Degrees of
freedom** here is the number of seeds minus one; with few seeds the bar a difference must clear is
higher.

Every scheduler runs the identical request file at a given seed, so the draw cancels and
the per-seed difference is the treatment effect alone. Comparing unpaired spreads instead
throws that away: the across-seed range is dominated by which requests were drawn, which is
exactly the variation the design removed. Worse, a min-max range *grows* with the number of
seeds, so more data makes a difference harder to detect rather than easier.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import sqrt
from statistics import mean, stdev

_T_CRITICAL_95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
                  6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}
"""Two-sided 95% Student's t critical value, keyed by degrees of freedom (n - 1).

A fixed multiple of the standard error, such as 2.0, is only close to a 95% interval at
large n. At n=10 (df=9) the true critical value is 2.262, and at n=3 (df=2) it is 4.303 --
more than double 2.0 -- so a fixed-k rule would call separations a proper test would not.
Every cell in this study is n=10, an exact hit in this table; the other entries are there
so an ad-hoc run at two to eleven seeds is exact too.
"""

_T_CRITICAL_95_LARGE = 1.960
"""Fallback beyond the table: the two-sided 95% *normal* critical value.

A documented approximation, not an exact value, and it is anti-conservative near the edge
of the table -- at df=11 the true critical value is 2.201 against this 1.960, 12% low, so
it would call a separation the t distribution would not. It converges from there (2.042 at
df=30, 2.009 at df=50), but nothing in this study reaches it: both sample sizes here are
exact table hits, and the fallback exists only so a larger ad-hoc run does not crash.
"""


@dataclass(frozen=True)
class Paired:
    """The difference between two arms on the seeds they share."""

    n: int
    """Number of shared seeds the difference was taken over."""
    mean: float
    """Mean per-seed advantage of the first arm. Positive means it is better."""
    se: float
    """Standard error of that mean."""

    def half_width(self) -> float:
        """Return the half-width of the two-sided 95% interval on ``mean``, in its units.

        The default ``separated()`` test is ``mean`` clearing this, so an interval drawn at
        this width crosses zero exactly when the difference is not separated.

        Returns:
            The t critical value for ``n - 1`` degrees of freedom times ``se``.
        """
        return _T_CRITICAL_95.get(self.n - 1, _T_CRITICAL_95_LARGE) * self.se

    def separated(self, k: float | None = None) -> bool:
        """Return True when the first arm's advantage clears ``k`` standard errors.

        ``k`` defaults to the two-sided 95% Student's t critical value for this
        comparison's own degrees of freedom (``n - 1``), not a fixed constant. That holds a
        three-seed cell (df=2, critical 4.303) to a much higher bar than a ten-seed one
        (df=9, critical 2.262). Pass an explicit ``k`` to override, for example to
        reproduce a fixed-threshold rule for comparison. Identical arms give mean 0 and are
        never separated, which is the correct answer for one algorithm compared with itself
        at a different exponent. The other degenerate case -- the same positive gap on every
        seed, so ``se`` is 0 and the t statistic is +inf -- separates at any ``k``, which is
        also correct: a difference that reproduces exactly is as separated as data gets.
        Integer metrics at few seeds make it reachable.

        Args:
            k: Standard errors the mean must exceed; ``None`` means the 95% t critical value
                for ``n - 1`` degrees of freedom.

        Returns:
            True if the mean advantage is positive and larger than ``k * se``.
        """
        # Look up the exact t value; fall back to the normal value beyond the table.
        if k is None:
            k = _T_CRITICAL_95.get(self.n - 1, _T_CRITICAL_95_LARGE)
        return self.mean > 0 and self.mean > k * self.se


def paired_diff(a: Mapping[int, float], b: Mapping[int, float]) -> Paired:
    """Compare two arms seed by seed on a time metric. Positive means ``a`` is better (lower).

    Raises when fewer than two seeds are shared, because a standard error needs two points.

    Args:
        a: The first arm's metric value per seed.
        b: The second arm's metric value per seed.

    Returns:
        The mean and standard error of ``a``'s per-seed advantage over ``b``.

    Raises:
        ValueError: ``a`` and ``b`` share fewer than two seeds.
    """
    # Only seeds both arms ran on can be paired.
    seeds = sorted(set(a) & set(b))
    if len(seeds) < 2:
        raise ValueError(f"need at least two shared seeds, got {len(seeds)}")
    # Smaller is better, so ``b - a`` is ``a``'s advantage.
    diffs = [b[s] - a[s] for s in seeds]
    spread = stdev(diffs)
    return Paired(
        n=len(diffs),
        mean=mean(diffs),
        se=spread / sqrt(len(diffs)),
    )
