from __future__ import annotations

import re
from typing import Iterable, List

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value.strip()))


def parse_recipients(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,;\n\r]+", value)
    parsed: list[str] = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        if item not in parsed:
            parsed.append(item)
    return parsed


def ensure_valid_recipients(recipients: Iterable[str]) -> list[str]:
    normalized = [r.strip() for r in recipients if r and r.strip()]
    invalid = [r for r in normalized if not is_valid_email(r)]
    if invalid:
        raise ValueError(f"Invalid recipient emails: {', '.join(invalid)}")
    return normalized


def compute_window_metrics(prices: list[float], current: float) -> dict[str, float]:
    if not prices:
        raise ValueError("Price list is empty")
    high = max(prices)
    low = min(prices)
    delta = high - low
    pct = (delta / low * 100.0) if low else 0.0
    return {
        "current": round(current, 4),
        "high": round(high, 4),
        "low": round(low, 4),
        "delta": round(delta, 4),
        "pct": round(pct, 4),
    }


def compute_badge(current: float, high: float, low: float, near_extreme_pct: float) -> str:
    if high <= 0 or low <= 0:
        return "区间内"
    # near extreme threshold is in percent units, e.g. 0.10
    low_dist = abs(current - low) / low * 100.0
    high_dist = abs(high - current) / high * 100.0
    if low_dist <= near_extreme_pct and low_dist <= high_dist:
        return "近低点"
    if high_dist <= near_extreme_pct and high_dist < low_dist:
        return "近高点"
    return "区间内"


def should_trigger(delta: float, pct: float, threshold_delta: float, threshold_pct: float) -> bool:
    return delta >= threshold_delta or pct >= threshold_pct


def threshold_progress(delta: float, pct: float, threshold_delta: float, threshold_pct: float) -> float:
    ratios: list[float] = []
    if threshold_delta > 0:
        ratios.append(delta / threshold_delta)
    if threshold_pct > 0:
        ratios.append(pct / threshold_pct)
    return max(ratios, default=0.0)


def is_near_threshold(
    delta: float,
    pct: float,
    threshold_delta: float,
    threshold_pct: float,
    adaptive_threshold_ratio: float,
) -> bool:
    return threshold_progress(delta, pct, threshold_delta, threshold_pct) >= adaptive_threshold_ratio


def alert_direction(current: float, high: float, low: float, badge: str) -> str:
    if badge == "近高点":
        return "high"
    if badge == "近低点":
        return "low"
    if abs(high - current) < abs(current - low):
        return "high"
    return "low"


def has_extreme_breakthrough(
    direction: str,
    current: dict,
    previous: dict | None,
    min_delta: float = 2.0,
) -> bool:
    if previous is None:
        return True
    if direction == "high":
        return float(current.get("high_price", 0)) - float(previous.get("high_price", 0)) > min_delta
    return float(previous.get("low_price", 0)) - float(current.get("low_price", 0)) > min_delta
