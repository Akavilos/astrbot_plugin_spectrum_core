"""按指定时区生成时间区间、时段和近似日照描述。"""


from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def now(tz_name: str = "Asia/Shanghai") -> datetime:
    return datetime.now(tz(tz_name))


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def period(dt: datetime) -> str:
    h = dt.hour
    if h < 5:
        return "深夜"
    if h < 8:
        return "清晨"
    if h < 11:
        return "上午"
    if h < 13:
        return "中午"
    if h < 17:
        return "下午"
    if h < 19:
        return "傍晚"
    if h < 23:
        return "夜里"
    return "深夜"


def _sun_arc(lat_deg: float, dt: datetime) -> tuple[float, float]:
    n = dt.timetuple().tm_yday
    decl = -23.44 * math.cos(math.radians(360.0 / 365.0 * (n + 10)))
    lat_r = math.radians(lat_deg)
    d_r = math.radians(decl)
    cos_ha = -math.tan(lat_r) * math.tan(d_r)
    cos_ha = max(-1.0, min(1.0, cos_ha))
    half_arc_hours = math.degrees(math.acos(cos_ha)) / 15.0

    return 12.0 - half_arc_hours, 12.0 + half_arc_hours


def daylight(dt: datetime, lat_deg: float) -> str:
    if lat_deg is None:
        return "未知"
    sunrise, sunset = _sun_arc(lat_deg, dt)
    h = dt.hour + dt.minute / 60.0
    if h < sunrise - 0.5:
        return "天还没亮"
    if h < sunrise + 1.0:
        return "刚亮不久"
    if h < sunset - 1.5:
        return "白天，光线充足"
    if h < sunset - 0.25:
        return "天色开始转暗"
    if h < sunset + 0.5:
        return "黄昏，将暗未暗"
    if h < sunset + 1.5:
        return "刚入夜"
    return "夜色已深"


def build_interval(
    from_dt: datetime,
    now_dt: datetime,
    lat_deg: float | None,
) -> str:
    span_min = max(0, int((now_dt - from_dt).total_seconds() // 60))
    if span_min < 90:
        span_txt = f"{span_min} 分钟"
    elif span_min < 60 * 36:
        span_txt = f"{span_min / 60:.1f} 小时"
    else:
        span_txt = f"{span_min / 60 / 24:.1f} 天"

    lines = [
        "── 时间锚点（权威，以下文为准，不要凭上下文猜时间）──",
        f"上一刻：{iso(from_dt)}（{period(from_dt)}）",
        f"现在　：{iso(now_dt)}（{_WEEKDAYS[now_dt.weekday()]} {period(now_dt)}）",
        f"区间长度：{span_txt}",
        f"此刻日照：{daylight(now_dt, lat_deg)}",
        "",
        f"你要补写的是「{iso(from_dt)} → {iso(now_dt)}」这段时间，并**停在此刻**。",
    ]
    return "\n".join(lines)


def default_from(now_dt: datetime, last_ts: datetime | None) -> datetime:
    if last_ts is not None and last_ts <= now_dt:
        return last_ts
    return now_dt - timedelta(minutes=20)
