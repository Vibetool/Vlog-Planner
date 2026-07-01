"""Shared helpers for vlog-planner scripts.

Pure Python standard library only — no pip installs. Provides:
  - config loading (config.json -> config.example.json -> built-in defaults)
  - http_get / http_post with a polite User-Agent, timeout and 429/5xx backoff
  - wgs84_to_gcj02: convert OSM/GPS coords to the datum Chinese maps use
  - small output helpers

Network access is only used by the data-source scripts; sun.py needs none of it.
"""
import json
import os
import sys
import time
import math
import urllib.parse
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Identify ourselves to OSM/Nominatim/Overpass (their usage policy REQUIRES this).
USER_AGENT = "vlog-planner/0.1 (+https://github.com/Vibetool/Vlog-Planner; community Claude Code skill)"

_DEFAULT_CONFIG = {
    "region": "auto",
    "language": "zh-CN",
    "providers": {
        "weather": {"provider": "open-meteo",
                    "qweather": {"key": "", "base": "https://devapi.qweather.com"},
                    "openweather": {"key": ""}},
        "geocode": {"provider": "nominatim", "amap": {"key": ""}},
        "poi": {"provider": "overpass", "radius_m": 8000, "amap": {"key": ""}},
        "sun": {"provider": "builtin"},
    },
    "planning": {
        "shootable_threshold": "balanced",
        "daily_window": ["06:00", "20:00"],
        "max_spots_per_day": 4,
        "pace": "relaxed",
        "prefer_golden_hour": True,
    },
    "style": {"vibe": "治愈", "gear": ["主相机 + 广角", "长焦镜头", "DJI Osmo Pocket 3", "手机"]},
}


def _deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if k.startswith("_"):
            continue  # ignore "_comment"/"_help" annotation keys
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=None):
    """Load config.json if present, else config.example.json, merged over defaults."""
    cfg = json.loads(json.dumps(_DEFAULT_CONFIG))  # deep copy
    candidates = []
    if path:
        candidates.append(path)
    candidates += [os.path.join(ROOT, "config.json"), os.path.join(ROOT, "config.example.json")]
    for p in candidates:
        if p and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    user = json.load(f)
                return _deep_merge(cfg, user)
            except Exception as e:
                # a malformed file should not silently drop us to bare defaults;
                # warn and fall through to the next candidate (e.g. the example).
                eprint(f"warning: could not read config {p}: {e}; trying next candidate")
                continue
    return cfg


def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


def _request(url, data=None, headers=None, timeout=30, retries=3):
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = data.encode("utf-8") if isinstance(data, str) else data
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt + 1)
                continue
            # surface API error bodies (Open-Meteo returns {"error":true,"reason":...})
            try:
                detail = e.read().decode("utf-8")
            except Exception:
                detail = ""
            raise RuntimeError(f"HTTP {e.code} for {url[:120]}... {detail[:300]}")
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt + 1)
                continue
            raise RuntimeError(f"network error for {url[:120]}...: {e}")
    raise RuntimeError(f"request failed: {last_err}")


def http_get(base, params=None, headers=None, timeout=30, retries=3):
    url = base
    if params:
        url = base + ("&" if "?" in base else "?") + urllib.parse.urlencode(params)
    return _request(url, data=None, headers=headers, timeout=timeout, retries=retries)


def http_post(url, data, headers=None, timeout=60, retries=3):
    """POST form-encoded data (used for Overpass: data='[out:json];...')."""
    h = {"Content-Type": "application/x-www-form-urlencoded"}
    if headers:
        h.update(headers)
    if isinstance(data, dict):
        data = urllib.parse.urlencode(data)
    return _request(url, data=data, headers=h, timeout=timeout, retries=retries)


# --- China coordinate datum conversion (WGS-84 -> GCJ-02) -------------------
# OSM / Nominatim / Overpass / GPS return WGS-84. Chinese consumer maps (Amap,
# Baidu, Tencent) and official basemaps use the legally-mandated GCJ-02 offset
# (~100-300 m). Convert before users cross-check a point on a Chinese map.
_A = 6378245.0          # semi-major axis (krasovsky 1940)
_EE = 0.00669342162296594323  # eccentricity squared


def _out_of_china(lat, lon):
    return not (0.8293 <= lat <= 55.8271 and 72.004 <= lon <= 137.8347)


def _transform_lat(x, y):
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x, y):
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat, lon):
    """Return (gcj_lat, gcj_lon). Unchanged outside mainland China."""
    if _out_of_china(lat, lon):
        return lat, lon
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return round(lat + dlat, 6), round(lon + dlon, 6)


def gcj02_to_wgs84(lat, lon):
    """Approximate inverse (one-step). Sub-metre-to-few-metre error — fine for
    planning. Amap/Baidu return GCJ-02; convert back so our canonical coords
    stay WGS-84 (what OSM, GPS and Open-Meteo expect)."""
    if _out_of_china(lat, lon):
        return lat, lon
    glat, glon = wgs84_to_gcj02(lat, lon)
    return round(lat * 2 - glat, 6), round(lon * 2 - glon, 6)


def emit(obj):
    """Print a JSON result to stdout (UTF-8, human-readable)."""
    print(json.dumps(obj, ensure_ascii=False, indent=2))
