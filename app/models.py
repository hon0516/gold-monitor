from __future__ import annotations

import os
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .logic import ensure_valid_recipients


SMTPMode = Literal["none", "starttls", "ssl"]


def _env_value(name: str, fallback_name: str | None = None, default: str = "") -> str:
    return os.getenv(f"GOLD_MONITOR_{name}") or (os.getenv(fallback_name) if fallback_name else None) or default


def _env_int(name: str, fallback_name: str | None, default: int) -> int:
    raw = _env_value(name, fallback_name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class SMTPSettings(BaseModel):
    host: str = ""
    port: int = 587
    security: SMTPMode = "starttls"
    username: str = ""
    password: str = ""
    sender_name: str = "浙商积存金提醒"
    sender_email: str = ""
    recipients: list[str] = Field(default_factory=list)
    subject_prefix: str = "[浙商]"

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if value <= 0 or value > 65535:
            raise ValueError("SMTP port must be between 1 and 65535")
        return value

    @field_validator("recipients")
    @classmethod
    def validate_recipients(cls, value: list[str]) -> list[str]:
        return ensure_valid_recipients(value)

    @classmethod
    def from_env_defaults(cls) -> "SMTPSettings":
        return cls(
            host=_env_value("SMTP_HOST", "SMTP_HOST"),
            port=_env_int("SMTP_PORT", "SMTP_PORT", 587),
            security=_env_value("SMTP_SECURITY", "SMTP_SECURITY", "starttls"),
            username=_env_value("SMTP_USERNAME", "SMTP_USERNAME"),
            password=_env_value("SMTP_PASSWORD", "SMTP_PASSWORD"),
            sender_name=_env_value("SMTP_SENDER_NAME", "SMTP_SENDER_NAME", "浙商积存金提醒"),
            sender_email=_env_value("SMTP_SENDER_EMAIL", "SMTP_SENDER_EMAIL"),
            subject_prefix=_env_value("SMTP_SUBJECT_PREFIX", "SMTP_SUBJECT_PREFIX", "[浙商]"),
        )

    def with_env_defaults(self) -> "SMTPSettings":
        defaults = SMTPSettings.from_env_defaults()
        data = self.model_dump()
        use_env_profile = not data.get("host") and bool(defaults.host)
        for key, value in defaults.model_dump().items():
            if key == "recipients":
                continue
            if use_env_profile and value not in ("", None):
                data[key] = value
            elif data.get(key) in ("", None):
                data[key] = value
        return SMTPSettings.model_validate(data)


class AlertSettings(BaseModel):
    threshold_delta: float = 3.0
    threshold_pct: float = 0.30
    near_extreme_pct: float = 0.10
    cooldown_minutes: int = 60
    poll_interval_seconds: int = 30
    fast_poll_interval_seconds: int = 5
    adaptive_threshold_ratio: float = 0.70
    extreme_breakthrough_delta: float = 2.0
    retention_days: int = 7

    @field_validator(
        "threshold_delta",
        "threshold_pct",
        "near_extreme_pct",
        "adaptive_threshold_ratio",
        "extreme_breakthrough_delta",
    )
    @classmethod
    def non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("Threshold values must be non-negative")
        return value

    @field_validator("cooldown_minutes", "poll_interval_seconds", "fast_poll_interval_seconds", "retention_days")
    @classmethod
    def positive_ints(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Value must be positive")
        return value

    @model_validator(mode="after")
    def validate_adaptive_intervals(self) -> "AlertSettings":
        if self.fast_poll_interval_seconds > self.poll_interval_seconds:
            raise ValueError("Fast poll interval must be less than or equal to normal poll interval")
        return self


class BankSourceConfig(BaseModel):
    code: str
    name: str
    product_sku: str
    order_source: str = ""
    latest_url: str
    today_url: str = ""
    latest_method: Literal["GET", "POST"] = "POST"
    today_method: Literal["GET", "POST"] = "POST"


class AppConfig(BaseModel):
    smtp: SMTPSettings = Field(default_factory=SMTPSettings)
    alert: AlertSettings = Field(default_factory=AlertSettings)
    timezone: str = "Asia/Shanghai"
    product_sku: str = "1961543816"
    order_source: str = "swj_zsjcj_0102"
    enabled_sources: list[str] = Field(default_factory=lambda: ["zheshang"])
    enabled: bool = True


class SMTPSettingsUpdate(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    security: Optional[SMTPMode] = None
    username: Optional[str] = None
    password: Optional[str] = None
    sender_name: Optional[str] = None
    sender_email: Optional[str] = None
    recipients: Optional[list[str]] = None
    subject_prefix: Optional[str] = None


class AlertSettingsUpdate(BaseModel):
    threshold_delta: Optional[float] = None
    threshold_pct: Optional[float] = None
    near_extreme_pct: Optional[float] = None
    cooldown_minutes: Optional[int] = None
    poll_interval_seconds: Optional[int] = None
    fast_poll_interval_seconds: Optional[int] = None
    adaptive_threshold_ratio: Optional[float] = None
    extreme_breakthrough_delta: Optional[float] = None
    retention_days: Optional[int] = None


class ConfigUpdate(BaseModel):
    smtp: Optional[SMTPSettingsUpdate] = None
    alert: Optional[AlertSettingsUpdate] = None
    enabled_sources: Optional[list[str]] = None
    enabled: Optional[bool] = None


class TestEmailRequest(BaseModel):
    recipients: Optional[list[str]] = None


class CheckResult(BaseModel):
    source_code: str = ""
    source_name: str = ""
    triggered: bool
    near_threshold: bool = False
    threshold_progress: float = 0
    badge: str
    current_price: float
    high_price: float
    low_price: float
    delta: float
    pct: float
    sampled_points: int
    run_time: str
    mail_sent: bool = False
    mail_skipped_reason: str = ""
    error: str = ""


class MonitorRunResult(BaseModel):
    triggered: bool
    near_threshold: bool = False
    threshold_progress: float = 0
    run_time: str
    results: list[CheckResult] = Field(default_factory=list)
    error: str = ""
