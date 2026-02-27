#!/usr/bin/env python3
from __future__ import annotations

from datetime import date, timedelta

from btcsignal_daily import MIN_HOLD, compute_all_states


def _synthetic_dates(n: int) -> list[str]:
    start = date(2025, 1, 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _scenario_breaker_before_min_hold() -> tuple[list[str], list[float]]:
    # Warm-up stable zone (avoids early breaker), then short HOLD, then sharp crash.
    closes: list[float] = [100.0] * 100
    closes += [101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112]
    closes += [100, 95, 92, 90, 89, 88]
    return _synthetic_dates(len(closes)), closes


def test_force_exit_priority() -> None:
    dates, closes = _scenario_breaker_before_min_hold()
    states = compute_all_states(dates, closes)

    hold_entry_idx = None
    for i in range(1, len(states)):
        if states[i - 1]["state"] == "CASH" and states[i]["state"] == "HOLD":
            hold_entry_idx = i
            break
    assert hold_entry_idx is not None, "No CASH->HOLD transition found in synthetic scenario"

    breaker_exit_idx = None
    for i in range(1, len(states)):
        if states[i]["reason"] == "crash_breaker_fired":
            breaker_exit_idx = i
            break
    assert breaker_exit_idx is not None, "No crash_breaker_fired found in synthetic scenario"
    assert states[breaker_exit_idx - 1]["state"] == "HOLD", "Breaker fired without prior HOLD; invalid scenario"
    assert states[breaker_exit_idx]["state"] == "CASH", "Breaker day must force immediate CASH exit"

    hold_days = breaker_exit_idx - hold_entry_idx
    assert hold_days < MIN_HOLD, "Scenario is not testing pre-MIN_HOLD breaker behavior"


def test_compute_all_states_stable() -> None:
    dates, closes = _scenario_breaker_before_min_hold()
    run1 = [(s["date"], s["state"], s["reason"]) for s in compute_all_states(dates, closes)]
    run2 = [(s["date"], s["state"], s["reason"]) for s in compute_all_states(dates, closes)]
    assert run1 == run2, "compute_all_states must be deterministic/stable"


if __name__ == "__main__":
    test_force_exit_priority()
    test_compute_all_states_stable()
    print("PASS: force-exit priority + stable recompute")
