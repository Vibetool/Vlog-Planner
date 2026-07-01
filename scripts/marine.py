#!/usr/bin/env python3
"""Sea state for a COASTAL point — Open-Meteo Marine API (free, no key).

Covers the "潮汐 / 海况" factor 柴西 lists alongside 天气/光照/日出日落, for
beach / 海边 / 赶海 / 海岛日出 shoots: wave & swell height, direction, period,
and sea-surface temperature.

IMPORTANT: this returns SEA STATE (waves/swell/temp), NOT the astronomical
TIDE height. Tide timing (退潮拍滩涂倒影/赶海, 涨潮拍浪) needs a local 潮汐表
(tide table) for the dates — look it up separately (many coastal 景区 / 潮汐
app / WebSearch "<地点> 潮汐表 <日期>"). This script fills the sea-state half.

Usage:
  python3 marine.py --lat 36.06 --lon 120.38 --start 2026-07-03 --end 2026-07-04
Output JSON: {lat, lon, timezone, is_coastal, hourly:{date:[{time, wave_m,
  wave_dir, wave_period_s, swell_m, sea_temp_c}]}, note}
"""
import argparse

import _common as C

MARINE = "https://marine-api.open-meteo.com/v1/marine"
HOURLY = ["wave_height", "wave_direction", "wave_period", "swell_wave_height",
          "sea_surface_temperature"]


def get_marine(lat, lon, start, end):
    d = C.http_get(MARINE, params={
        "latitude": lat, "longitude": lon, "hourly": ",".join(HOURLY),
        "timezone": "auto", "start_date": start, "end_date": end,
    })
    if d.get("error"):
        raise RuntimeError(f"open-meteo marine: {d.get('reason')}")
    h = d.get("hourly", {})
    times = h.get("time", [])
    hourly = {}
    any_wave = False
    for i, t in enumerate(times):
        date, hm = t.split("T")
        wave = _at(h.get("wave_height"), i)
        if isinstance(wave, (int, float)):
            any_wave = True
        hourly.setdefault(date, []).append({
            "time": hm[:5],
            "wave_m": wave,
            "wave_dir": _at(h.get("wave_direction"), i),
            "wave_period_s": _at(h.get("wave_period"), i),
            "swell_m": _at(h.get("swell_wave_height"), i),
            "sea_temp_c": _at(h.get("sea_surface_temperature"), i),
        })
    note = ("Open-Meteo Marine（免费、无 key）。浪高/涌浪/海温——**不含潮汐涨落时刻**，"
            "赶海/滩涂请另查当地潮汐表。")
    if not any_wave:
        note = "该坐标无海况数据（可能不临海/在内陆）。海况仅对海边拍摄有意义。"
    return {"lat": lat, "lon": lon, "timezone": d.get("timezone"),
            "is_coastal": any_wave, "hourly": hourly, "note": note}


def _at(arr, i):
    return arr[i] if isinstance(arr, list) and i < len(arr) else None


def main():
    p = argparse.ArgumentParser(description="Coastal sea state (waves/swell/sea-temp) for beach shooting.")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    args = p.parse_args()
    C.emit(get_marine(args.lat, args.lon, args.start, args.end))


if __name__ == "__main__":
    main()
