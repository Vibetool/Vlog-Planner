#!/usr/bin/env python3
"""Discover nearby scenic / photogenic spots around a point.

Providers (config providers.poi.provider):
  - overpass : free OpenStreetMap Overpass API. DEFAULT. Tested. China POI
               coverage is PATCHY — often returns few or unnamed spots, so
               treat results as hints on top of the route you already give.
  - amap     : 高德周边搜索 (China). Needs a key. Implemented from Amap docs but
               UNVERIFIED at build time.
  - none     : skip discovery; rely only on the user's route.

Output JSON: {center:{lat,lon}, radius_m, provider, count, spots:[{name, lat,
              lon, gcj02, kind, ele, tags_summary, has_wiki}], note}
"""
import argparse

import _common as C

OVERPASS = "https://overpass-api.de/api/interpreter"

# (key, value, human kind). Order = rough priority for shot-worthiness.
SCENIC = [
    ("tourism", "viewpoint", "观景台"),
    ("natural", "peak", "山峰"),
    ("waterway", "waterfall", "瀑布"),
    ("natural", "waterfall", "瀑布"),
    ("natural", "glacier", "冰川"),
    ("natural", "water", "水域/湖"),
    ("tourism", "attraction", "景点"),
    ("historic", "*", "人文古迹"),
    ("leisure", "park", "公园"),
    ("man_made", "tower", "塔/观景塔"),
]


def build_query(lat, lon, radius, limit):
    # coerce to numbers so nothing but digits/floats reach the Overpass QL string
    lat, lon, radius, limit = float(lat), float(lon), int(radius), int(limit)
    clauses = []
    for k, v, _ in SCENIC:
        sel = f'["{k}"]' if v == "*" else f'["{k}"="{v}"]'
        clauses.append(f"  nwr{sel}(around:{radius},{lat},{lon});")
    body = "[out:json][timeout:25];\n(\n" + "\n".join(clauses) + f"\n);\nout center {limit};"
    return body


def kind_for(tags):
    for k, v, label in SCENIC:
        if v == "*" and k in tags:
            return label
        if tags.get(k) == v:
            return label
    return "其他"


def fetch_overpass(lat, lon, radius, limit):
    q = build_query(lat, lon, radius, limit)
    data = C.http_post(OVERPASS, data={"data": q})
    spots = []
    for e in data.get("elements", []):
        if e.get("type") == "node":
            plat, plon = e.get("lat"), e.get("lon")
        else:
            c = e.get("center") or {}
            plat, plon = c.get("lat"), c.get("lon")
        if plat is None or plon is None:
            continue
        tags = e.get("tags", {})
        name = tags.get("name:zh") or tags.get("name") or tags.get("name:en")
        glat, glon = C.wgs84_to_gcj02(plat, plon)
        has_wiki = bool(tags.get("wikidata") or tags.get("wikipedia"))
        spots.append({
            "name": name,
            "lat": round(plat, 6), "lon": round(plon, 6),
            "gcj02": {"lat": glat, "lon": glon},
            "kind": kind_for(tags),
            "ele": _num(tags.get("ele")),
            "tags_summary": _summ(tags),
            "has_wiki": has_wiki,
            "named": bool(name),
        })
    # rank: named+wiki first, then named, then by elevation desc
    spots.sort(key=lambda s: (s["named"], s["has_wiki"], s["ele"] or 0), reverse=True)
    return spots


def fetch_amap(lat, lon, radius, limit, key):
    """高德周边搜索. UNVERIFIED at build time — needs a key."""
    glat, glon = C.wgs84_to_gcj02(lat, lon)
    data = C.http_get("https://restapi.amap.com/v3/place/around", params={
        "key": key, "location": f"{glon:.6f},{glat:.6f}", "radius": radius,
        "types": "110000|110200|110100", "offset": min(limit, 25), "page": 1,
        "extensions": "base",
    })
    if str(data.get("status")) != "1":
        raise RuntimeError(f"amap around error: {data.get('info')}")
    spots = []
    for poi in data.get("pois", []):
        glon2, glat2 = (float(x) for x in poi["location"].split(","))  # GCJ-02
        wlat, wlon = C.gcj02_to_wgs84(glat2, glon2)
        spots.append({
            "name": poi.get("name"),
            "lat": round(wlat, 6), "lon": round(wlon, 6),
            "gcj02": {"lat": round(glat2, 6), "lon": round(glon2, 6)},
            "kind": poi.get("type", "").split(";")[-1] or "景点",
            "ele": None, "tags_summary": poi.get("type"), "has_wiki": False, "named": True,
        })
    return spots


def discover(lat, lon, cfg, radius=None, limit=30):
    pc = cfg["providers"]["poi"]
    provider = pc.get("provider", "overpass")
    radius = radius or pc.get("radius_m", 8000)
    if provider == "none":
        return {"center": {"lat": lat, "lon": lon}, "radius_m": radius, "provider": "none",
                "count": 0, "spots": [], "note": "POI 自动发现已关闭，仅用你给的路线。"}
    if provider == "amap":
        key = (pc.get("amap") or {}).get("key", "")
        if key:
            spots = fetch_amap(lat, lon, radius, limit, key)
            return {"center": {"lat": lat, "lon": lon}, "radius_m": radius, "provider": "amap",
                    "count": len(spots), "spots": spots,
                    "note": "高德周边搜索（未在构建时实测）。"}
        C.eprint("poi provider=amap but no key — using overpass.")
    spots = fetch_overpass(lat, lon, radius, limit)
    named = sum(1 for s in spots if s["named"])
    note = (f"OpenStreetMap Overpass。半径 {radius/1000:.0f}km 内找到 {len(spots)} 处（其中 {named} 处有名称）。"
            "中国 OSM 覆盖稀疏，很多点缺名甚至缺失——请把结果当作你既定路线之外的补充提示，不要当作全集。"
            "© OpenStreetMap contributors")
    return {"center": {"lat": lat, "lon": lon}, "radius_m": radius, "provider": "overpass",
            "count": len(spots), "spots": spots, "note": note}


def _summ(tags):
    keys = ["tourism", "natural", "historic", "leisure", "waterway", "man_made", "ele"]
    return ", ".join(f"{k}={tags[k]}" for k in keys if k in tags)


def _num(v):
    try:
        return float(str(v).split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def main():
    p = argparse.ArgumentParser(description="Find nearby scenic spots around a coordinate.")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--radius", type=int, default=None, help="metres (default from config, 8000)")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--config", default=None)
    args = p.parse_args()
    cfg = C.load_config(args.config)
    C.emit(discover(args.lat, args.lon, cfg, radius=args.radius, limit=args.limit))


if __name__ == "__main__":
    main()
