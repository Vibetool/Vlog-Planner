#!/usr/bin/env python3
"""Fetch weather for a point over a date range, shaped for shot planning.

Providers (config providers.weather.provider):
  - open-meteo : free, no key, global. DEFAULT. Tested. Auto-picks the
                 forecast endpoint (up to ~16 days ahead) or the archive
                 endpoint (ERA5 reanalysis, past dates).
  - qweather   : 和风天气 (China). Needs a key. Implemented from QWeather's
                 public REST API but UNVERIFIED at build time — verify first.
  - openweather: placeholder dispatch; falls back to open-meteo until a key
                 and implementation are added.

Output JSON:
  {lat, lon, timezone, provider, endpoint,
   daily:  [{date, sunrise, sunset, tmax, tmin, precip_sum, precip_prob_max,
             weathercode, weather}],
   hourly: {"YYYY-MM-DD": [{time:"HH:MM", temp, cloud, precip, precip_prob,
             wind, visibility_km, weathercode, weather}]},
   note}
"""
import argparse
import datetime as dt

import _common as C

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

HOURLY = ["temperature_2m", "cloudcover", "precipitation", "precipitation_probability",
          "windspeed_10m", "visibility", "weathercode"]
DAILY = ["sunrise", "sunset", "temperature_2m_max", "temperature_2m_min",
         "precipitation_sum", "precipitation_probability_max", "weathercode"]

# WMO weather code -> short Chinese description.
WMO_ZH = {
    0: "晴", 1: "大致晴朗", 2: "局部多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "小阵雨", 81: "阵雨", 82: "暴阵雨", 85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴强冰雹",
}


def wmo(code):
    return WMO_ZH.get(code, f"未知({code})") if code is not None else "—"


def _today():
    return dt.date.today()


def _pick_endpoint(start, end):
    """Return (url, use_probability). Archive for past dates, forecast otherwise."""
    today = _today()
    if end < today - dt.timedelta(days=2):
        return ARCHIVE_URL, False  # archive has no precipitation_probability
    return FORECAST_URL, True


def fetch_open_meteo(lat, lon, start, end):
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)
    url, use_prob = _pick_endpoint(start_d, end_d)
    is_archive = url == ARCHIVE_URL
    # ERA5 archive has neither precipitation_probability nor visibility.
    hourly = [h for h in HOURLY if (use_prob or h != "precipitation_probability")
              and not (is_archive and h == "visibility")]
    daily = [d for d in DAILY if use_prob or d != "precipitation_probability_max"]
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": ",".join(hourly), "daily": ",".join(daily),
        "timezone": "auto", "start_date": start, "end_date": end,
    }
    data = C.http_get(url, params=params)
    if data.get("error"):
        raise RuntimeError(f"open-meteo: {data.get('reason')}")
    return data, url, use_prob


def shape_open_meteo(data, use_prob):
    tz = data.get("timezone")
    tz_off = data.get("utc_offset_seconds", 0) / 3600.0
    d = data.get("daily", {})
    daily = []
    for i, date in enumerate(d.get("time", [])):
        code = _at(d.get("weathercode"), i)
        daily.append({
            "date": date,
            "sunrise": _hm(_at(d.get("sunrise"), i)),
            "sunset": _hm(_at(d.get("sunset"), i)),
            "tmax": _at(d.get("temperature_2m_max"), i),
            "tmin": _at(d.get("temperature_2m_min"), i),
            "precip_sum": _at(d.get("precipitation_sum"), i),
            "precip_prob_max": _at(d.get("precipitation_probability_max"), i) if use_prob else None,
            "weathercode": code,
            "weather": wmo(code),
        })
    h = data.get("hourly", {})
    times = h.get("time", [])
    hourly = {}
    for i, t in enumerate(times):
        date, hm = t.split("T")
        code = _at(h.get("weathercode"), i)
        vis = _at(h.get("visibility"), i)
        hourly.setdefault(date, []).append({
            "time": hm[:5],
            "temp": _at(h.get("temperature_2m"), i),
            "cloud": _at(h.get("cloudcover"), i),
            "precip": _at(h.get("precipitation"), i),
            "precip_prob": _at(h.get("precipitation_probability"), i) if use_prob else None,
            "wind": _at(h.get("windspeed_10m"), i),
            "visibility_km": round(vis / 1000.0, 1) if isinstance(vis, (int, float)) else None,
            "weathercode": code,
            "weather": wmo(code),
        })
    return tz, tz_off, daily, hourly


def fetch_qweather(lat, lon, start, end, key, base):
    """和风天气. UNVERIFIED at build time — needs a key to test.

    Free tier serves a 7-day daily forecast and a 24-hour hourly forecast, so
    multi-day hourly is only available for ~today. Returns the same schema with
    partial hourly; plan.py degrades to daily scoring where hourly is absent.
    """
    loc = f"{lon:.4f},{lat:.4f}"  # QWeather wants lon,lat
    daily_raw = C.http_get(f"{base}/v7/weather/7d", params={"location": loc, "key": key})
    if str(daily_raw.get("code")) != "200":
        raise RuntimeError(f"qweather 7d error code={daily_raw.get('code')}")
    start_d, end_d = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    daily = []
    for day in daily_raw.get("daily", []):
        date = day.get("fxDate")
        if not (start_d <= dt.date.fromisoformat(date) <= end_d):
            continue
        daily.append({
            "date": date, "sunrise": day.get("sunrise"), "sunset": day.get("sunset"),
            "tmax": _num(day.get("tempMax")), "tmin": _num(day.get("tempMin")),
            "precip_sum": _num(day.get("precip")), "precip_prob_max": None,
            "weathercode": None, "weather": day.get("textDay"),
        })
    hourly = {}
    try:
        hr = C.http_get(f"{base}/v7/weather/24h", params={"location": loc, "key": key})
        for x in hr.get("hourly", []):
            date, hm = x.get("fxTime", "T")[:16].split("T")
            hourly.setdefault(date, []).append({
                "time": hm[:5], "temp": _num(x.get("temp")), "cloud": _num(x.get("cloud")),
                "precip": _num(x.get("precip")), "precip_prob": _num(x.get("pop")),
                "wind": _num(x.get("windSpeed")), "visibility_km": _num(x.get("vis")),
                "weathercode": None, "weather": x.get("text"),
            })
    except Exception as e:
        C.eprint(f"qweather hourly unavailable: {e}")
    return "Asia/Shanghai", daily, hourly


def get_weather(lat, lon, start, end, cfg):
    w = cfg["providers"]["weather"]
    provider = w.get("provider", "open-meteo")
    if provider == "qweather":
        key = (w.get("qweather") or {}).get("key", "")
        if key:
            base = (w.get("qweather") or {}).get("base", "https://devapi.qweather.com")
            tz, daily, hourly = fetch_qweather(lat, lon, start, end, key, base)
            return {"lat": lat, "lon": lon, "timezone": tz, "utc_offset_hours": 8.0,
                    "provider": "qweather", "endpoint": base, "daily": daily, "hourly": hourly,
                    "note": "和风天气（未在构建时实测）。免费版仅 ~7天日预报 + 24h 逐时。"}
        C.eprint("weather provider=qweather but no key — using open-meteo.")
    # open-meteo (default / fallback)
    data, url, use_prob = fetch_open_meteo(lat, lon, start, end)
    tz, tz_off, daily, hourly = shape_open_meteo(data, use_prob)
    note = ("Open-Meteo（免费、无需 key）。" +
            ("历史 ERA5 再分析（无降水概率/能见度，故历史日不计入能见度评分）。" if url == ARCHIVE_URL
             else "未来预报，最长约 16 天。") +
            "中国为全球模式驱动，复杂山地（如高原午后对流、河谷雾）精度有限，仅供参考。")
    return {"lat": lat, "lon": lon, "timezone": tz, "utc_offset_hours": tz_off,
            "provider": "open-meteo", "endpoint": url, "daily": daily, "hourly": hourly, "note": note}


def _at(arr, i):
    return arr[i] if isinstance(arr, list) and i < len(arr) else None


def _hm(s):
    return s.split("T")[1][:5] if s and "T" in s else s


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    p = argparse.ArgumentParser(description="Weather for a point over a date range (golden-hour-friendly).")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--config", default=None)
    args = p.parse_args()
    cfg = C.load_config(args.config)
    C.emit(get_weather(args.lat, args.lon, args.start, args.end, cfg))


if __name__ == "__main__":
    main()
