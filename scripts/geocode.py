#!/usr/bin/env python3
"""Resolve a place name to coordinates.

Providers (set in config providers.geocode.provider):
  - nominatim : free, no key (OpenStreetMap). DEFAULT. Tested.
  - amap      : 高德 (China only, far better China coverage). Needs a key.
                Implemented from Amap's public REST API but UNVERIFIED here
                (no key available at build time) — verify before relying on it.

All coordinates are normalised to WGS-84 (what GPS / Open-Meteo / OSM use);
`gcj02` holds the same point shifted to the datum Chinese maps display, so you
can paste it into 高德/百度 and land on the right spot.

Usage:
  python3 geocode.py "稻城亚丁" [--limit 1] [--lang zh-CN] [--cn] [--config path]
Output: JSON {query, provider, results:[{name, display_name, lat, lon, gcj02,
        type, category, importance, source}], note}
"""
import argparse
import math

import _common as C

NOMINATIM = "https://nominatim.openstreetmap.org/search"


def _viewbox(lat, lon, km):
    """Nominatim viewbox 'lon_w,lat_n,lon_e,lat_s' around a centre point."""
    dlat = km / 111.0
    dlon = km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    return f"{lon - dlon:.5f},{lat + dlat:.5f},{lon + dlon:.5f},{lat - dlat:.5f}"


def _nominatim_query(query, limit, lang, cn_only, viewbox=None, bounded=False):
    params = {
        "q": query, "format": "jsonv2", "limit": max(1, limit),
        "accept-language": lang, "addressdetails": 1,
    }
    if cn_only:
        params["countrycodes"] = "cn"
    if viewbox:
        params["viewbox"] = viewbox
        if bounded:
            params["bounded"] = 1
    return C.http_get(NOMINATIM, params=params)


def geocode_nominatim(query, limit=1, lang="zh-CN", cn_only=False, near=None, box_km=80):
    """If `near`=(lat,lon) is given, disambiguate to that region: first restrict
    strictly to a box around it, then fall back to a soft bias, then unconstrained."""
    data = []
    if near:
        vb = _viewbox(near[0], near[1], box_km)
        data = _nominatim_query(query, limit, lang, cn_only, viewbox=vb, bounded=True)
        if not data:
            data = _nominatim_query(query, limit, lang, cn_only, viewbox=vb, bounded=False)
    if not data:
        data = _nominatim_query(query, limit, lang, cn_only)
    results = []
    for r in data:
        lat = float(r["lat"])
        lon = float(r["lon"])
        glat, glon = C.wgs84_to_gcj02(lat, lon)
        results.append({
            "name": r.get("name") or r.get("display_name", "").split(",")[0],
            "display_name": r.get("display_name"),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "gcj02": {"lat": glat, "lon": glon},
            "type": r.get("type"),
            "category": r.get("category"),
            "importance": round(float(r.get("importance", 0)), 3),
            "source": "nominatim",
        })
    return results


def geocode_amap(query, key, limit=1):
    # NOTE: implemented from Amap docs, not live-tested. Amap returns GCJ-02
    # "lon,lat"; we convert back to WGS-84 for our canonical coords.
    data = C.http_get("https://restapi.amap.com/v3/geocode/geo",
                      params={"key": key, "address": query, "output": "json"})
    if str(data.get("status")) != "1":
        raise RuntimeError(f"amap geocode error: {data.get('info')}")
    results = []
    for g in (data.get("geocodes") or [])[:max(1, limit)]:
        glon, glat = (float(x) for x in g["location"].split(","))  # GCJ-02
        wlat, wlon = C.gcj02_to_wgs84(glat, glon)
        results.append({
            "name": g.get("formatted_address") or query,
            "display_name": g.get("formatted_address"),
            "lat": round(wlat, 6),
            "lon": round(wlon, 6),
            "gcj02": {"lat": round(glat, 6), "lon": round(glon, 6)},
            "type": g.get("level"),
            "category": "amap",
            "importance": None,
            "source": "amap",
        })
    return results


def geocode(query, cfg, limit=1, cn_only=False, near=None, box_km=80):
    gc = cfg["providers"]["geocode"]
    provider = gc.get("provider", "nominatim")
    lang = cfg.get("language", "zh-CN")
    note = None
    if provider == "amap":
        key = (gc.get("amap") or {}).get("key", "")
        if key:
            return geocode_amap(query, key, limit), "amap (China-optimised)"
        provider = "nominatim"
        note = "providers.geocode.provider=amap but no key set — fell back to free Nominatim."
    results = geocode_nominatim(query, limit=limit, lang=lang, cn_only=cn_only, near=near, box_km=box_km)
    base_note = "OpenStreetMap/Nominatim. China POI coverage is patchy; coords are WGS-84 (gcj02 field = Chinese-map datum)."
    return results, (note + " " + base_note if note else base_note)


def main():
    p = argparse.ArgumentParser(description="Geocode a place name to coordinates.")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--lang", default=None, help="override config language, e.g. zh-CN")
    p.add_argument("--cn", action="store_true", help="restrict to China (countrycodes=cn)")
    p.add_argument("--near", default=None, help="'lat,lon' — disambiguate to spots near this point (e.g. a destination's sub-spots)")
    p.add_argument("--box-km", type=float, default=80, help="half-size of the --near search box in km")
    p.add_argument("--config", default=None)
    args = p.parse_args()
    cfg = C.load_config(args.config)
    if args.lang:
        cfg["language"] = args.lang
    near = None
    if args.near:
        near = tuple(float(x) for x in args.near.split(","))
    results, note = geocode(args.query, cfg, limit=args.limit, cn_only=args.cn, near=near, box_km=args.box_km)
    effective = results[0]["source"] if results else cfg["providers"]["geocode"]["provider"]
    C.emit({"query": args.query, "provider": effective, "results": results, "note": note})


if __name__ == "__main__":
    main()
