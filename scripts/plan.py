#!/usr/bin/env python3
"""vlog-planner orchestrator.

Takes a day-by-day route, enriches every (spot, date) with:
  - golden-hour / blue-hour windows and the sun's rise/set direction (sun.py)
  - the weather inside those windows (weather.py)
  - a 0-5 "出片指数" shootability score + a plan-B flag (柴西's logic)

Outputs ONE structured JSON object. The Markdown shooting manual is written by
Claude from this JSON (so it can add the place's story / A-roll / B-roll), per
references/methodology.md.

Input (JSON via --input FILE or stdin):
  {
    "days": [
      {"date": "2026-07-02", "spots": ["稻城亚丁", "牛奶海"]},
      {"date": "2026-07-03", "spots": [{"name": "新都桥", "lat": 30.06, "lon": 101.49}]}
    ]
  }
Spots may be a bare name (geocoded) or an object with lat/lon (used as-is).
"""
import argparse
import datetime as dt
import json
import sys
import time

import _common as C
import geocode as G
import weather as W
import sun as S
import poi as P

CUTS = {"lenient": 1.3, "balanced": 2.3, "strict": 3.3}
# precip-probability % at/above which an hour counts as "wet" for the
# dry-window / rain-onset scans (looser->stricter with the shootable口味).
PP_CUTS = {"lenient": 60, "balanced": 50, "strict": 40}
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def verdict(score):
    if score is None:
        return "—", "无数据"
    if score >= 4.3:
        return "★★★★★", "绝佳出片窗口"
    if score >= 3.3:
        return "★★★★", "光线/天气不错"
    if score >= 2.3:
        return "★★★", "一般，可拍"
    if score >= 1.3:
        return "★★", "勉强，建议备选"
    return "★", "不建议，改期或改点"


def score_window(agg):
    if agg is None:
        return None
    s = 5.0
    pp = agg.get("precip_prob") or 0
    precip = agg.get("precip") or 0
    cloud = agg.get("cloud") or 0
    vis = agg.get("visibility_km")
    if precip >= 2 or pp >= 80:
        s -= 3
    elif precip >= 0.5 or pp >= 60:
        s -= 2
    elif pp >= 40:
        s -= 1
    if cloud >= 90:
        s -= 1.5
    elif cloud >= 70:
        s -= 0.8
    elif cloud <= 10:
        s -= 0.2  # bald clear sky a touch less interesting at golden hour
    if vis is not None:
        if vis < 1:
            s -= 3      # whiteout / 你就在云里 — effectively unshootable
        elif vis < 2:
            s -= 2
        elif vis < 5:
            s -= 1
    return round(max(0.0, min(5.0, s)), 1)


def aggregate(hours, start_hm, end_hm):
    """Aggregate hourly weather over the clock-hours covering [start,end]."""
    if not hours:
        return None
    sh, eh = int(start_hm[:2]), int(end_hm[:2])
    sel = [h for h in hours if sh <= int(h["time"][:2]) <= eh]
    if not sel:
        return None

    def vals(key):
        return [h[key] for h in sel if isinstance(h.get(key), (int, float))]

    def avg(key):
        v = vals(key)
        return round(sum(v) / len(v), 1) if v else None

    def mx(key):
        v = vals(key)
        return max(v) if v else None

    def sm(key):
        v = vals(key)
        return round(sum(v), 1) if v else None

    def mn(key):
        v = vals(key)
        return min(v) if v else None

    worst = max(sel, key=lambda h: ((h.get("precip") or 0), (h.get("precip_prob") or 0)))
    return {
        "from": sel[0]["time"], "to": sel[-1]["time"],
        "cloud": avg("cloud"), "precip_prob": mx("precip_prob"),
        "precip": sm("precip"), "wind": mx("wind"),
        "visibility_km": mn("visibility_km"),
        "weather": worst.get("weather"), "weathercode": worst.get("weathercode"),
    }


def _hours_in(hours, start_hm, end_hm):
    sh, eh = int(start_hm[:2]), int(end_hm[:2])
    return [h for h in hours if sh <= int(h["time"][:2]) <= eh]


def _is_dry(h, pp_cut):
    if (h.get("precip") or 0) >= 0.5:
        return False
    pp = h.get("precip_prob")
    return not (pp is not None and pp >= pp_cut)


def best_dry_window(hours, day_start, day_end, pp_cut):
    """Longest contiguous 'dry enough to shoot/hike' run within daylight
    [day_start, day_end]. Answers: the daily precip_prob_max hides a clear
    morning — where exactly is it? Returns {start,end,hours,cloud,precip_prob} or None."""
    best, run = None, []

    def flush():
        nonlocal best
        if not run:
            return
        clouds = [x["cloud"] for x in run if isinstance(x.get("cloud"), (int, float))]
        pps = [x["precip_prob"] for x in run if isinstance(x.get("precip_prob"), (int, float))]
        cand = {"start": run[0]["time"], "end": run[-1]["time"], "hours": len(run),
                "cloud": round(sum(clouds) / len(clouds)) if clouds else None,
                "precip_prob": max(pps) if pps else None}
        if best is None or cand["hours"] > best["hours"]:
            best = cand

    for h in _hours_in(hours, day_start, day_end):
        if _is_dry(h, pp_cut):
            run.append(h)
        else:
            flush()
            run = []
    flush()
    return best


def rain_onset(hours, after_hm, day_end, pp_cut):
    """First daylight hour STRICTLY AFTER after_hm (pass the dry window's end)
    where rain kicks in — pairs with best_dry_window as '拍到这里、之后转雨'.
    Returns 'HH:MM' or None (stays dry). Requiring a preceding dry window (the
    caller passes best_dry_window['end']) avoids the false 'rain starts at X,
    wrap up before then' on days that were already raining."""
    ah, eh = int(after_hm[:2]), int(day_end[:2])
    for h in hours:
        hh = int(h["time"][:2])
        if ah < hh <= eh and not _is_dry(h, pp_cut):
            return h["time"]
    return None


def make_window(label, span, hours, light_dir=None, light_az=None):
    if not span or span[0] is None or span[1] is None:
        return None  # no window, or a half-open high-latitude golden window
    agg = aggregate(hours, span[0], span[1])
    if agg is None:
        return None
    sc = score_window(agg)
    stars, vtext = verdict(sc)
    return {
        "label": label, "start": span[0], "end": span[1],
        "light_direction": light_dir, "light_azimuth": light_az,
        "weather": agg, "score": sc, "stars": stars, "verdict": vtext,
    }


def enrich_spot(name, query, lat, lon, geo_meta, date, wx, threshold, daily_window=("06:00", "20:00")):
    tz = wx.get("utc_offset_hours", 8.0)
    sun = S.day_summary_json(lat, lon, date, tz)
    sunrise_dir = S.azimuth_to_compass(sun["sunrise_azimuth"])
    sunset_dir = S.azimuth_to_compass(sun["sunset_azimuth"])
    hours = wx.get("hourly", {}).get(date, [])
    day = next((d for d in wx.get("daily", []) if d["date"] == date), {})
    glat, glon = C.wgs84_to_gcj02(lat, lon)

    windows = {}
    w_am = make_window("日出黄金时刻", sun["golden_hour_morning"], hours, sunrise_dir, sun["sunrise_azimuth"])
    w_pm = make_window("日落黄金时刻", sun["golden_hour_evening"], hours, sunset_dir, sun["sunset_azimuth"])
    w_day = make_window("白天时段", list(daily_window), hours)
    if w_am:
        windows["golden_am"] = w_am
    if w_pm:
        windows["golden_pm"] = w_pm
    if w_day:
        windows["daytime"] = w_day

    # Intraday intelligence: where's the actual dry window, when does rain start.
    pp_cut = PP_CUTS.get(threshold, 50)
    day_start = sun["sunrise"] or "06:00"
    day_end = sun["sunset"] or "20:00"
    bdw = best_dry_window(hours, day_start, day_end, pp_cut) if hours else None
    day_windows = {
        "best_dry_window": bdw,
        # onset only makes sense as "the rain that ends the dry window"
        "rain_onset": rain_onset(hours, bdw["end"], day_end, pp_cut) if (hours and bdw) else None,
    }

    cut = CUTS.get(threshold, CUTS["balanced"])
    golden = [w for w in (windows.get("golden_pm"), windows.get("golden_am")) if w]
    if golden:
        best = max(golden, key=lambda w: w["score"])
    elif windows.get("daytime"):
        best = windows["daytime"]
    else:
        best = None

    if best is not None:
        plan_b = best["score"] < cut
        ld = f"，太阳在{best['light_direction']}({int(best['light_azimuth'])}°)" if best.get("light_direction") else ""
        aw = best["weather"]
        reason = (f"{best['label']} {best['start']}–{best['end']}{ld}；"
                  f"云量{_p(aw['cloud'])}/降水概率{_p(aw['precip_prob'])}/能见度{_km(aw['visibility_km'])} "
                  f"→ {best['stars']} {best['verdict']}")
        recommended = {"window": best["label"], "time": f"{best['start']}–{best['end']}",
                       "score": best["score"], "stars": best["stars"], "reason": reason,
                       "plan_b": plan_b}
    else:
        # No hourly (e.g. qweather multi-day) — fall back to day-level guidance.
        pp = day.get("precip_prob_max")
        plan_b = bool(pp and pp >= 60)
        recommended = {"window": "（仅日级数据）", "time": f"{sun['sunrise']}–{sun['sunset']}",
                       "score": None, "stars": "—",
                       "reason": f"无逐时数据，按日级：{day.get('weather','—')}，降水概率{_p(pp)}。"
                                 f"建议黄金时刻 {(sun['golden_hour_evening'] or ['—','—'])[0]} 前后。",
                       "plan_b": plan_b}

    return {
        "name": name, "query": query,
        "lat": round(lat, 6), "lon": round(lon, 6),
        "gcj02": {"lat": glat, "lon": glon},
        "geocode": geo_meta,
        "sun": {
            "sunrise": sun["sunrise"], "sunset": sun["sunset"],
            "solar_noon": sun["solar_noon"], "noon_elevation": sun["solar_noon_elevation"],
            "golden_morning": sun["golden_hour_morning"], "golden_evening": sun["golden_hour_evening"],
            "blue_dawn": sun["blue_hour_dawn"], "blue_dusk": sun["blue_hour_dusk"],
            "sunrise_azimuth": sun["sunrise_azimuth"], "sunrise_direction": sunrise_dir,
            "sunset_azimuth": sun["sunset_azimuth"], "sunset_direction": sunset_dir,
            "polar_day": sun["polar_day"], "polar_night": sun["polar_night"],
        },
        "day_weather": {
            "tmax": day.get("tmax"), "tmin": day.get("tmin"),
            "precip_sum": day.get("precip_sum"), "precip_prob_max": day.get("precip_prob_max"),
            "weather": day.get("weather"),
        },
        "windows": windows,
        "day_windows": day_windows,
        "recommended": recommended,
    }


def _p(v):
    return "—" if v is None else f"{int(round(v))}%"


def _km(v):
    return "—" if v is None else f"{v}km"


def classify_day(spots):
    """柴西 method: tag each day A-roll / 抢窗 / B-roll / 赶路.

    A single clear golden window must NOT label an all-day-stormy day an
    'A-roll 重点日' (a real bug); gate the top label on day-level wetness and,
    when a good window sits inside a wet day, call it a 抢窗日 and point at the
    concrete dry window."""
    scored = [s for s in spots
              if s.get("recommended") and s["recommended"].get("score") is not None]
    if not scored:
        all_planb = all(s.get("recommended", {}).get("plan_b") for s in spots) if spots else True
        if all_planb:
            return "赶路·休整日", "天气欠佳或无足够数据，建议赶路/转场/室内，把好天气留给其他天。"
        return "B-roll 空镜日", "数据有限，按一般日处理，以环境空镜为主。"

    best = max(s["recommended"]["score"] for s in scored)
    pp_max = max((s.get("day_weather", {}).get("precip_prob_max") or 0) for s in scored)
    precip_sum = max((s.get("day_weather", {}).get("precip_sum") or 0) for s in scored)
    rain_dominated = pp_max >= 70 or precip_sum >= 8

    ref = next((s for s in scored if s.get("day_windows", {}).get("best_dry_window")), None)
    dw = ref["day_windows"]["best_dry_window"] if ref else None
    onset = ref["day_windows"]["rain_onset"] if ref else None
    hint = ""
    if dw:
        hint = f" 最佳干窗 {dw['start']}–{dw['end']}。"
        if onset:
            hint += f" 约 {onset} 起转雨，之前收工。"

    if best >= 3.3 and not rain_dominated:
        return "A-roll 重点拍摄日", "天气光线俱佳，安排叙事主线与人物故事。" + hint
    if best >= 3.3 and rain_dominated:
        return "抢窗日", "有好光窗口但当天多雨——把拍摄压到干窗、雨前收工。" + hint
    if best >= 2.3:
        return "B-roll 空镜日", "光线一般，重点拍环境/细节空镜提升质感。" + hint
    return "赶路·休整日", "全天出片条件差，建议赶路/转场或改拍雨雾情绪向、室内/市集。" + hint


def plan(inp, cfg, include_poi=False):
    days_in = inp.get("days") or []
    if not days_in:
        return {"error": "input has no days", "hint": '需要 {"days":[{"date":"YYYY-MM-DD","spots":[...]}]}',
                "days": []}
    dates = sorted(d["date"] for d in days_in)
    start, end = dates[0], dates[-1]
    threshold = cfg["planning"].get("shootable_threshold", "balanced")
    daily_window = cfg["planning"].get("daily_window", ["09:00", "20:00"])

    geo_cache = {}
    wx_cache = {}
    quality = set()
    geo_calls = 0
    cn = cfg.get("region") == "china"
    effective = {}  # provider actually used (may differ from config after fallback)

    # Anchor = a centre point to disambiguate sub-spots to (a destination's
    # nearby sights, e.g. resolve "冰湖" to 雨崩's, not 西藏's). Explicit via
    # input "anchor" (name or {lat,lon}); otherwise auto-set to the first
    # spot we resolve, so later spots in the same route bias toward it.
    anchor = {"coord": None}

    def _spot_geocode(name, near):
        nonlocal geo_calls
        if geo_calls > 0:
            time.sleep(1.1)  # honour Nominatim 1 req/s
        results, note = G.geocode(name, cfg, limit=1, cn_only=cn, near=near)
        geo_calls += 1
        quality.add("选址：" + note)
        return results

    a = inp.get("anchor")
    if isinstance(a, dict) and a.get("lat") is not None:
        anchor["coord"] = (float(a["lat"]), float(a["lon"]))
    elif isinstance(a, str) and a.strip():
        res = _spot_geocode(a, None)
        if res:
            anchor["coord"] = (res[0]["lat"], res[0]["lon"])

    def resolve(spot):
        if isinstance(spot, dict) and spot.get("lat") is not None:
            c = (float(spot["lat"]), float(spot["lon"]))
            if anchor["coord"] is None:
                anchor["coord"] = c
            return spot.get("name", "(自定义点)"), spot.get("name"), c[0], c[1], {"source": "user"}
        name = spot if isinstance(spot, str) else spot.get("name")
        if name in geo_cache:
            return geo_cache[name]
        near = anchor["coord"]
        ns = spot.get("near") if isinstance(spot, dict) else None
        if isinstance(ns, (list, tuple)) and len(ns) == 2:
            near = (float(ns[0]), float(ns[1]))
        elif isinstance(ns, dict) and ns.get("lat") is not None:
            near = (float(ns["lat"]), float(ns["lon"]))
        results = _spot_geocode(name, near)
        if not results:
            geo_cache[name] = (name, name, None, None, {"source": "none", "error": "未找到该地点"})
            return geo_cache[name]
        r = results[0]
        effective["geocode"] = r["source"]
        meta = {"source": r["source"], "matched": r.get("display_name"), "type": r.get("type"),
                "near_anchor": list(near) if near else None}
        if anchor["coord"] is None:
            anchor["coord"] = (r["lat"], r["lon"])
        geo_cache[name] = (r.get("name") or name, name, r["lat"], r["lon"], meta)
        return geo_cache[name]

    def weather_for(lat, lon):
        key = (round(lat, 2), round(lon, 2))
        if key not in wx_cache:
            wx = W.get_weather(lat, lon, start, end, cfg)
            effective["weather"] = wx.get("provider")
            quality.add("天气：" + wx.get("note", ""))
            wx_cache[key] = wx
        return wx_cache[key]

    poi_cache = {}

    def poi_for(lat, lon):
        """Named nearby vantage points as source-tagged B-roll 机位 candidates."""
        key = (round(lat, 2), round(lon, 2))
        if key not in poi_cache:
            try:
                res = P.discover(lat, lon, cfg, radius=3000)
                named = [s for s in res.get("spots", []) if s.get("named")][:8]
                poi_cache[key] = [{"name": s["name"], "kind": s["kind"], "gcj02": s["gcj02"],
                                   "has_wiki": s["has_wiki"], "source": res.get("provider")}
                                  for s in named]
                effective["poi"] = res.get("provider")
                quality.add("机位：" + res.get("note", ""))
            except Exception as e:
                C.eprint(f"poi 取用失败（跳过）：{e}")
                poi_cache[key] = []
        return poi_cache[key]

    out_days = []
    for d in days_in:
        date = d["date"]
        wd = WEEKDAYS[dt.date.fromisoformat(date).weekday()]
        spots_out = []
        for spot in d.get("spots", []):
            name, query, lat, lon, meta = resolve(spot)
            if lat is None:
                spots_out.append({"name": name, "query": query, "error": meta.get("error"),
                                  "geocode": meta})
                continue
            wx = weather_for(lat, lon)
            sp = enrich_spot(name, query, lat, lon, meta, date, wx, threshold, daily_window)
            if include_poi:
                sp["nearby_poi"] = poi_for(lat, lon)
            spots_out.append(sp)
        role, role_note = classify_day(spots_out)
        out_days.append({"date": date, "weekday": wd, "day_role": role,
                         "day_role_note": role_note, "spots": spots_out})

    quality.add("光线：日出/日落/黄金时刻/太阳方位为本地天文计算（NOAA），不含地形遮挡，海拔按海平面地平线。")
    if cfg.get("region") in ("china", "auto"):
        quality.add("坐标：内部统一 WGS-84；gcj02 字段为高德/百度等国内地图所用坐标（差 100–300m），核对位置请用 gcj02。")

    return {
        "generated_for": {"date_range": [start, end], "days": len(days_in),
                          "threshold": threshold},
        "config_summary": {
            "region": cfg.get("region"), "language": cfg.get("language"),
            # only the providers plan.py actually exercises; reports the EFFECTIVE
            # provider used (after any no-key fallback), not just what was configured.
            # POI only appears when --poi was used.
            "providers": {
                "weather": effective.get("weather", cfg["providers"]["weather"].get("provider")),
                "geocode": effective.get("geocode", cfg["providers"]["geocode"].get("provider") + " (未调用)"),
                **({"poi": effective["poi"]} if "poi" in effective else {}),
                "sun": "builtin",
            },
            "style": cfg.get("style"),
        },
        "data_quality_notes": sorted(quality),
        "days": out_days,
    }


def main():
    p = argparse.ArgumentParser(description="Plan a vlog shoot from a day-by-day route.")
    p.add_argument("--input", default=None, help="JSON file ({days:[...]}); reads stdin if omitted")
    p.add_argument("--config", default=None)
    p.add_argument("--poi", action="store_true", help="also attach named nearby vantage points (机位) per spot (extra Overpass calls)")
    args = p.parse_args()
    raw = open(args.input, encoding="utf-8").read() if args.input else sys.stdin.read()
    inp = json.loads(raw)
    cfg = C.load_config(args.config)
    C.emit(plan(inp, cfg, include_poi=args.poi))


if __name__ == "__main__":
    main()
