from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

import requests

from .logic import compute_badge, compute_window_metrics, should_trigger
from .models import BankSourceConfig

API_HOST = "https://api.jdjygold.com"
MS_API_HOST = "https://ms.jr.jd.com"

AVAILABLE_SOURCES: dict[str, BankSourceConfig] = {
    "zheshang": BankSourceConfig(
        code="zheshang",
        name="浙商",
        product_sku="1961543816",
        order_source="swj_zsjcj_0102",
        latest_url=f"{MS_API_HOST}/gw2/generic/jrm/h5/m/stdLatestPrice",
        today_url=f"{MS_API_HOST}/gw2/generic/jrm/h5/m/stdTodayLatestPrices",
    ),
    "minsheng": BankSourceConfig(
        code="minsheng",
        name="民生",
        product_sku="P005",
        latest_url=f"{API_HOST}/gw/generic/hj/h5/m/latestPrice",
        latest_method="GET",
    ),
    "icbc": BankSourceConfig(
        code="icbc",
        name="工银",
        product_sku="2005453243",
        latest_url=f"{API_HOST}/gw2/generic/jrm/h5/m/icbcLatestPrice",
        today_url=f"{API_HOST}/gw2/generic/jrm/h5/m/icbcTodayLatestPrices",
    ),
}


@dataclass
class MarketSnapshot:
    source_code: str
    source_name: str
    product_sku: str
    order_source: str
    current_price: float
    high_price: float
    low_price: float
    delta: float
    pct: float
    badge: str
    sampled_points: int
    run_time: datetime
    triggered: bool


def get_enabled_sources(codes: list[str]) -> list[BankSourceConfig]:
    sources = [AVAILABLE_SOURCES[code] for code in codes if code in AVAILABLE_SOURCES]
    return sources or [AVAILABLE_SOURCES["zheshang"]]


def _request_json(source: BankSourceConfig, url: str, method: str, timeout: int = 15) -> dict:
    params = {"productSku": source.product_sku}
    if source.order_source:
        params["orderSource"] = source.order_source
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "referer": "https://m.jdjygold.com/",
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15",
    }

    if method == "GET":
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
    else:
        response = requests.post(url, headers=headers, params=params, json={"reqData": params}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _parse_time_point(value: str, tz: ZoneInfo) -> datetime:
    # usually format: 2026-04-23 00:00:00
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=tz)
    except ValueError:
        # fallback when backend returns timestamp strings
        return datetime.fromtimestamp(int(value) / 1000.0, tz=tz)


def _extract_prices(items: Iterable[dict], tz: ZoneInfo) -> list[tuple[datetime, float]]:
    points: list[tuple[datetime, float]] = []
    for item in items:
        raw = item.get("value") or []
        if isinstance(raw, list) and len(raw) >= 2:
            ts_raw = str(raw[0])
            price_raw = raw[1]
        else:
            ts_raw = str(item.get("name", ""))
            price_raw = item.get("price")

        if price_raw is None:
            continue
        try:
            dt = _parse_time_point(ts_raw, tz)
            price = float(price_raw)
        except (ValueError, TypeError):
            continue
        points.append((dt, price))
    return points


def fetch_snapshot(
    *,
    source: BankSourceConfig,
    threshold_delta: float,
    threshold_pct: float,
    near_extreme_pct: float,
    timezone: str,
    local_points: list[tuple[datetime, float]] | None = None,
) -> MarketSnapshot:
    tz = ZoneInfo(timezone)

    latest_resp = _request_json(source, source.latest_url, source.latest_method)
    latest = latest_resp.get("resultData", {}).get("datas", {})
    current = float(latest["price"])
    latest_time = latest.get("time")
    if latest_time:
        now_dt = datetime.fromtimestamp(int(latest_time) / 1000.0, tz=tz)
    else:
        now_dt = datetime.now(tz)

    points: list[tuple[datetime, float]] = []
    if source.today_url:
        today_resp = _request_json(source, source.today_url, source.today_method)
        points = _extract_prices(today_resp.get("resultData", {}).get("datas", []), tz)
    if local_points:
        points.extend(local_points)
    points.append((now_dt, current))
    points.sort(key=lambda x: x[0])

    one_hour_ago = now_dt - timedelta(hours=1)
    window_prices = [price for dt, price in points if one_hour_ago <= dt <= now_dt]

    # fallback when there are not enough points in the last hour.
    if len(window_prices) < 2:
        window_prices = [price for _, price in points[-30:]]

    metrics = compute_window_metrics(window_prices, current)
    badge = compute_badge(
        current=metrics["current"],
        high=metrics["high"],
        low=metrics["low"],
        near_extreme_pct=near_extreme_pct,
    )

    triggered = should_trigger(
        delta=metrics["delta"],
        pct=metrics["pct"],
        threshold_delta=threshold_delta,
        threshold_pct=threshold_pct,
    )

    return MarketSnapshot(
        source_code=source.code,
        source_name=source.name,
        product_sku=source.product_sku,
        order_source=source.order_source,
        current_price=metrics["current"],
        high_price=metrics["high"],
        low_price=metrics["low"],
        delta=metrics["delta"],
        pct=metrics["pct"],
        badge=badge,
        sampled_points=len(window_prices),
        run_time=now_dt,
        triggered=triggered,
    )
