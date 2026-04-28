from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .db import Database
from .logic import alert_direction, has_extreme_breakthrough, is_near_threshold, threshold_progress
from .mailer import send_alert_email
from .market import fetch_snapshot, get_enabled_sources
from .models import CheckResult, MonitorRunResult


class MonitorService:
    def __init__(self, db: Database):
        self.db = db
        self.lock: asyncio.Lock | None = None
        self.last_result: dict = {}

    async def run_check(self, manual: bool = False) -> MonitorRunResult:
        if self.lock is None:
            self.lock = asyncio.Lock()
        async with self.lock:
            cfg = self.db.get_config()
            if not cfg.enabled and not manual:
                details = {"manual": manual, "reason": "monitor disabled"}
                self.db.add_run_log("SKIPPED", "Monitoring is disabled", details)
                result = MonitorRunResult(
                    triggered=False,
                    near_threshold=False,
                    threshold_progress=0,
                    run_time=datetime.now(ZoneInfo(cfg.timezone)).isoformat(),
                    results=[],
                    error="monitor disabled",
                )
                self.last_result = result.model_dump()
                return result

            results: list[CheckResult] = []
            sources = get_enabled_sources(cfg.enabled_sources)
            run_time = datetime.now(ZoneInfo(cfg.timezone)).isoformat()

            for source in sources:
                mail_sent = False
                skip_reason = ""
                error = ""
                try:
                    now_dt = datetime.now(ZoneInfo(cfg.timezone))
                    local_points = self.db.get_price_samples(source.code, now_dt - timedelta(hours=1))
                    snapshot = fetch_snapshot(
                        source=source,
                        threshold_delta=cfg.alert.threshold_delta,
                        threshold_pct=cfg.alert.threshold_pct,
                        near_extreme_pct=cfg.alert.near_extreme_pct,
                        timezone=cfg.timezone,
                        local_points=local_points,
                    )
                    self.db.add_price_sample(source.code, snapshot.run_time.isoformat(), snapshot.current_price)

                    progress = threshold_progress(
                        delta=snapshot.delta,
                        pct=snapshot.pct,
                        threshold_delta=cfg.alert.threshold_delta,
                        threshold_pct=cfg.alert.threshold_pct,
                    )
                    near_threshold = is_near_threshold(
                        delta=snapshot.delta,
                        pct=snapshot.pct,
                        threshold_delta=cfg.alert.threshold_delta,
                        threshold_pct=cfg.alert.threshold_pct,
                        adaptive_threshold_ratio=cfg.alert.adaptive_threshold_ratio,
                    )

                    current_payload = {
                        "run_time": snapshot.run_time.isoformat(),
                        "source_code": source.code,
                        "source_name": source.name,
                        "product_sku": source.product_sku,
                        "order_source": source.order_source,
                        "current_price": snapshot.current_price,
                        "high_price": snapshot.high_price,
                        "low_price": snapshot.low_price,
                        "delta": snapshot.delta,
                        "pct": snapshot.pct,
                        "badge": snapshot.badge,
                    }

                    if snapshot.triggered:
                        direction = alert_direction(
                            current=snapshot.current_price,
                            high=snapshot.high_price,
                            low=snapshot.low_price,
                            badge=snapshot.badge,
                        )
                        current_payload["direction"] = direction
                        last_sent_alert = self.db.get_last_sent_alert_by_source(source.code)
                        last_sent_at = (
                            datetime.fromisoformat(last_sent_alert["triggered_at"])
                            if last_sent_alert is not None
                            else None
                        )
                        now_dt = snapshot.run_time
                        if last_sent_at is not None:
                            cooldown_until = last_sent_at + timedelta(minutes=cfg.alert.cooldown_minutes)
                        else:
                            cooldown_until = None

                        if (
                            cooldown_until
                            and now_dt < cooldown_until
                            and not has_extreme_breakthrough(
                                direction,
                                current_payload,
                                last_sent_alert,
                                min_delta=cfg.alert.extreme_breakthrough_delta,
                            )
                        ):
                            skip_reason = "cooldown active"
                        elif not cfg.smtp.recipients:
                            skip_reason = "no recipients configured"
                        elif not cfg.smtp.host or not cfg.smtp.sender_email:
                            skip_reason = "smtp not configured"
                        else:
                            try:
                                send_alert_email(cfg, current_payload)
                                mail_sent = True
                            except Exception as exc:  # noqa: BLE001
                                error = str(exc)

                        should_record_alert = True
                        if skip_reason == "cooldown active":
                            last_alert = self.db.get_last_alert_event_by_direction(direction, source.code)
                            should_record_alert = (
                                last_alert is None
                                or now_dt - datetime.fromisoformat(last_alert["triggered_at"])
                                >= timedelta(minutes=1)
                            )

                        if should_record_alert:
                            self.db.add_alert_event(
                                payload={
                                    **current_payload,
                                    "sampled_points": snapshot.sampled_points,
                                    "manual": manual,
                                },
                                mail_sent=mail_sent,
                                mail_error=error or skip_reason,
                            )

                    results.append(
                        CheckResult(
                            source_code=source.code,
                            source_name=source.name,
                            triggered=snapshot.triggered,
                            near_threshold=near_threshold,
                            threshold_progress=round(progress, 4),
                            badge=snapshot.badge,
                            current_price=snapshot.current_price,
                            high_price=snapshot.high_price,
                            low_price=snapshot.low_price,
                            delta=snapshot.delta,
                            pct=snapshot.pct,
                            sampled_points=snapshot.sampled_points,
                            run_time=snapshot.run_time.isoformat(),
                            mail_sent=mail_sent,
                            mail_skipped_reason=skip_reason,
                            error=error,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                    results.append(
                        CheckResult(
                            source_code=source.code,
                            source_name=source.name,
                            triggered=False,
                            near_threshold=False,
                            threshold_progress=0,
                            badge="区间内",
                            current_price=0,
                            high_price=0,
                            low_price=0,
                            delta=0,
                            pct=0,
                            sampled_points=0,
                            run_time=datetime.now(ZoneInfo(cfg.timezone)).isoformat(),
                            mail_sent=False,
                            error=error,
                        )
                    )

            result = MonitorRunResult(
                triggered=any(item.triggered for item in results),
                near_threshold=any(item.near_threshold for item in results),
                threshold_progress=max((item.threshold_progress for item in results), default=0),
                run_time=run_time,
                results=results,
                error="; ".join(item.error for item in results if item.error),
            )
            self.db.add_run_log(
                "ERROR" if result.error and not any(item.current_price for item in results) else "OK",
                "Run completed",
                {"manual": manual, **result.model_dump()},
            )
            self.last_result = result.model_dump()
            return result
