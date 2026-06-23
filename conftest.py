"""Project-wide pytest configuration (loaded once at the repo root)."""

from __future__ import annotations

from hypothesis import HealthCheck, settings

# Hypothesis enforces a per-example deadline (200ms by default) measured in
# wall-clock time. Under our default `-n auto` xdist run, many workers
# saturate every core, so a trivial example can blow that budget purely from
# CPU scheduling contention and raise DeadlineExceeded -- a test that passes
# in isolation fails flakily in the full parallel suite (observed on
# test_persistence_reader.py::...test_percentiles_are_sorted_by_rank).
#
# Our property tests exercise pure functions where wall-clock-per-example
# carries no signal, so disable the deadline (and the matching too_slow
# health check) globally. Correctness is still asserted by every test body.
settings.register_profile(
    "default",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("default")
