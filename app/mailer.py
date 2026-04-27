from __future__ import annotations

import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr

from .models import AppConfig


def _format_display_time(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value)


def _build_test_subject(cfg: AppConfig) -> str:
    return f"{cfg.smtp.subject_prefix} 金价提醒 | 测试"


def _window_position_label(payload: dict) -> str:
    badge = payload.get("badge", "")
    if badge == "近高点":
        return "近1h高价"
    if badge == "近低点":
        return "近1h低价"

    current = float(payload["current_price"])
    high = float(payload["high_price"])
    low = float(payload["low_price"])
    if abs(high - current) < abs(current - low):
        return "近1h高价"
    return "近1h低价"


def _badge_colors(label: str) -> tuple[str, str]:
    if "高价" in label or label == "近高点":
        return "#fee2e2", "#b42318"
    return "#e7f2eb", "#176f3d"


def _subject_marker(label: str) -> str:
    if "高价" in label or label == "近高点":
        return "🔴"
    return "🟢"


def _build_alert_subject(cfg: AppConfig, payload: dict) -> str:
    current = f"{payload['current_price']:.2f}"
    label = _window_position_label(payload)
    source_name = payload.get("source_name") or "浙商"
    return f"{_subject_marker(label)}【{source_name}】金价 {current}元/克 | {label}"


def _build_alert_html(cfg: AppConfig, payload: dict) -> str:
    current = f"{payload['current_price']:.2f}"
    delta = f"{payload['delta']:.2f}"
    pct = f"{payload['pct']:.2f}%"
    high = f"{payload['high_price']:.2f}"
    low = f"{payload['low_price']:.2f}"
    badge = _window_position_label(payload)
    badge_bg, badge_fg = _badge_colors(badge)
    run_time = _format_display_time(payload.get("run_time", ""))
    source_name = payload.get("source_name") or "浙商"
    product_sku = payload.get("product_sku") or cfg.product_sku
    order_source = payload.get("order_source") or cfg.order_source

    trend_url = (
        f"https://m.jdjygold.com/finance-gold/gold-standard/home/"
        f"?productSku={product_sku}&orderSource={order_source}"
    )

    return f"""
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#eef7f2;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,'PingFang SC','Microsoft YaHei',sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#eef7f2;">
      <tr>
        <td align="center" style="padding:12px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;border-collapse:collapse;background:#ffffff;border:1px solid #d9e2d5;border-radius:12px;overflow:hidden;">
            <tr>
              <td style="padding:16px 16px 8px;">
                <div style="font-size:18px;line-height:1.35;font-weight:700;color:#111827;">【{source_name}】金价提醒</div>
                <div style="margin-top:6px;display:inline-block;background:{badge_bg};color:{badge_fg};border-radius:999px;padding:4px 10px;font-size:13px;line-height:1.4;font-weight:700;">{badge}</div>
              </td>
            </tr>
            <tr>
              <td style="padding:4px 16px 14px;">
                <span style="font-size:42px;line-height:1;font-weight:800;color:#0d8b52;">{current}</span>
                <span style="font-size:16px;line-height:1.2;font-weight:700;color:#374151;"> 元/克</span>
              </td>
            </tr>
            <tr>
              <td style="padding:0 16px 12px;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background:#f7faf6;border:1px solid #e2eadf;border-radius:10px;overflow:hidden;">
                  <tr>
                    <td style="padding:10px;border-bottom:1px solid #e2eadf;color:#5b6472;font-size:13px;">近 1h 差值</td>
                    <td align="right" style="padding:10px;border-bottom:1px solid #e2eadf;color:#111827;font-size:16px;font-weight:700;">{delta} 元/克</td>
                  </tr>
                  <tr>
                    <td style="padding:10px;border-bottom:1px solid #e2eadf;color:#5b6472;font-size:13px;">近 1h 幅度</td>
                    <td align="right" style="padding:10px;border-bottom:1px solid #e2eadf;color:#111827;font-size:16px;font-weight:700;">{pct}</td>
                  </tr>
                  <tr>
                    <td style="padding:10px;border-bottom:1px solid #e2eadf;color:#5b6472;font-size:13px;">近 1h 最高</td>
                    <td align="right" style="padding:10px;border-bottom:1px solid #e2eadf;color:#111827;font-size:16px;font-weight:700;">{high} 元/克</td>
                  </tr>
                  <tr>
                    <td style="padding:10px;color:#5b6472;font-size:13px;">近 1h 最低</td>
                    <td align="right" style="padding:10px;color:#111827;font-size:16px;font-weight:700;">{low} 元/克</td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:0 16px 16px;color:#5b6472;font-size:13px;line-height:1.6;">
                <div>告警时间：{run_time}</div>
                <div style="margin-top:6px;">近 12 小时走势：<a href="{trend_url}" style="color:#1d4ed8;word-break:break-all;">查看行情</a></div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
""".strip()


def _build_test_html() -> str:
    return """
    <html><body>
    <h2>金价提醒测试邮件</h2>
    <p>如果你收到这封邮件，说明 SMTP 配置可用。</p>
    </body></html>
    """.strip()


def _build_message(cfg: AppConfig, recipients: list[str], subject: str, html: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.smtp.sender_name, cfg.smtp.sender_email))
    msg["To"] = ", ".join(recipients)
    msg.set_content("请使用支持 HTML 的客户端查看邮件内容。")
    msg.add_alternative(html, subtype="html")
    return msg


def _smtp_send(cfg: AppConfig, message: EmailMessage) -> None:
    mode = cfg.smtp.security
    host = cfg.smtp.host
    port = cfg.smtp.port

    if mode == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=15)
    else:
        server = smtplib.SMTP(host, port, timeout=15)

    with server:
        server.ehlo()
        if mode == "starttls":
            server.starttls()
            server.ehlo()
        if cfg.smtp.username:
            server.login(cfg.smtp.username, cfg.smtp.password)
        server.send_message(message)


def send_alert_email(cfg: AppConfig, payload: dict, recipients: list[str] | None = None) -> None:
    recipients = recipients or cfg.smtp.recipients
    msg = _build_message(
        cfg=cfg,
        recipients=recipients,
        subject=_build_alert_subject(cfg, payload),
        html=_build_alert_html(cfg, payload),
    )
    _smtp_send(cfg, msg)


def send_test_email(cfg: AppConfig, recipients: list[str] | None = None) -> None:
    recipients = recipients or cfg.smtp.recipients
    msg = _build_message(
        cfg=cfg,
        recipients=recipients,
        subject=_build_test_subject(cfg),
        html=_build_test_html(),
    )
    _smtp_send(cfg, msg)
