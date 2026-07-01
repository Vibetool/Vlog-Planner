#!/usr/bin/env python3
"""
solar.py - Pure-stdlib NOAA solar-position calculator for a travel-vlog
"golden hour / sun direction" planner.

No third-party dependencies. Uses only ``math`` and ``datetime`` from the
Python standard library.

The math follows the NOAA Solar Calculator equations
(https://gml.noaa.gov/grad/solcalc/), which are a simplified implementation
of Jean Meeus, *Astronomical Algorithms*. Accuracy is on the order of ~1 min
for sunrise/sunset and a fraction of a degree for the sun's azimuth/elevation
over the years ~1900-2100 - more than good enough for planning photo shoots.

Public API
----------
- ``sun_position(lat, lon, dt_local, tz_offset_hours) -> (azimuth, elevation)``
- ``sun_position_utc(lat, lon, dt_utc) -> (azimuth, elevation)``
- ``sunrise_sunset(lat, lon, date, tz_offset_hours) -> dict``
- ``civil_twilight(lat, lon, date, tz_offset_hours) -> dict``  (blue hour)
- ``golden_hours(lat, lon, date, tz_offset_hours) -> dict``
- ``day_summary(lat, lon, date, tz_offset_hours) -> dict``  (everything at once)

All "local" datetimes are *naive* datetimes interpreted in the location's
local clock, and ``tz_offset_hours`` is that location's fixed UTC offset
(e.g. -7 for US Pacific Daylight Time, +5.5 for India, +9 for Japan).
Daylight-saving handling is the caller's responsibility (pass the offset that
is actually in effect on that date). Longitude is +East, latitude +North.

Angle thresholds (refraction-corrected apparent sun elevation):
    sunrise/sunset            : -0.833 deg (refraction + sun's semidiameter)
    civil twilight (blue hour): -6 deg
    golden hour               : -4 deg up to +6 deg
"""

from __future__ import annotations

import math
from datetime import datetime, date as _date, timedelta, timezone

__all__ = [
    "sun_position",
    "sun_position_utc",
    "sunrise_sunset",
    "civil_twilight",
    "golden_hours",
    "day_summary",
    "SUNRISE_SUNSET_ANGLE",
    "CIVIL_TWILIGHT_ANGLE",
    "GOLDEN_LOW_ANGLE",
    "GOLDEN_HIGH_ANGLE",
]

# ---------------------------------------------------------------------------
# Elevation thresholds (degrees)
# ---------------------------------------------------------------------------
SUNRISE_SUNSET_ANGLE = -0.833   # standard sunrise/sunset
CIVIL_TWILIGHT_ANGLE = -6.0     # civil twilight / "blue hour" boundary
GOLDEN_LOW_ANGLE = -4.0         # golden hour begins
GOLDEN_HIGH_ANGLE = 6.0         # golden hour ends


# ---------------------------------------------------------------------------
# Core NOAA solar geometry
# ---------------------------------------------------------------------------
def _julian_day(dt_utc: datetime) -> float:
    """Julian Day (including fractional day) for a UTC datetime.

    Valid for the Gregorian calendar (Fliegel / Meeus form).
    """
    year = dt_utc.year
    month = dt_utc.month
    day = (
        dt_utc.day
        + (dt_utc.hour + (dt_utc.minute + (dt_utc.second + dt_utc.microsecond / 1e6) / 60.0) / 60.0) / 24.0
    )
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + (a // 4)  # Gregorian correction
    jd = (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day + b - 1524.5
    )
    return jd


def _solar_geometry(jd: float):
    """Intermediate NOAA solar quantities for Julian Day ``jd``.

    Returns a dict with declination (deg) and equation of time (minutes).
    """
    # Julian century since J2000.0
    t = (jd - 2451545.0) / 36525.0

    # Geometric mean longitude of the sun (deg), normalized to [0, 360)
    geom_mean_long = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    if geom_mean_long < 0:
        geom_mean_long += 360.0

    # Geometric mean anomaly of the sun (deg)
    geom_mean_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)

    # Eccentricity of Earth's orbit
    eccent = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    # Sun's equation of center
    m_rad = math.radians(geom_mean_anom)
    sun_eq_ctr = (
        math.sin(m_rad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * m_rad) * (0.019993 - 0.000101 * t)
        + math.sin(3 * m_rad) * 0.000289
    )

    # Sun's true longitude (deg)
    sun_true_long = geom_mean_long + sun_eq_ctr

    # Sun's apparent longitude (deg), corrected for nutation/aberration
    omega = 125.04 - 1934.136 * t
    sun_app_long = sun_true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    # Mean obliquity of the ecliptic (deg)
    mean_obliq = (
        23.0
        + (26.0 + ((21.448 - t * (46.815 + t * (0.00059 - t * 0.001813)))) / 60.0) / 60.0
    )
    # Corrected obliquity (deg)
    obliq_corr = mean_obliq + 0.00256 * math.cos(math.radians(omega))

    # Sun's declination (deg)
    sin_decl = math.sin(math.radians(obliq_corr)) * math.sin(math.radians(sun_app_long))
    declination = math.degrees(math.asin(sin_decl))

    # Equation of time (minutes)
    var_y = math.tan(math.radians(obliq_corr / 2.0)) ** 2
    l0_rad = math.radians(geom_mean_long)
    eq_time = 4.0 * math.degrees(
        var_y * math.sin(2 * l0_rad)
        - 2.0 * eccent * math.sin(m_rad)
        + 4.0 * eccent * var_y * math.sin(m_rad) * math.cos(2 * l0_rad)
        - 0.5 * var_y * var_y * math.sin(4 * l0_rad)
        - 1.25 * eccent * eccent * math.sin(2 * m_rad)
    )

    return {"t": t, "declination": declination, "eq_time": eq_time}


# ---------------------------------------------------------------------------
# Sun position (azimuth / elevation) at an instant
# ---------------------------------------------------------------------------
def sun_position_utc(lat: float, lon: float, dt_utc: datetime):
    """Sun azimuth & elevation for a UTC datetime.

    Parameters
    ----------
    lat, lon : float
        Latitude (deg, +N) and longitude (deg, +E; e.g. -122.4 for San Francisco).
    dt_utc : datetime
        Instant in UTC. May be naive (assumed UTC) or tz-aware.

    Returns
    -------
    (azimuth, elevation) : tuple of float
        ``azimuth`` is degrees clockwise from true north (0 = N, 90 = E,
        180 = S, 270 = W). ``elevation`` is the refraction-corrected apparent
        altitude in degrees above the horizon (negative = below horizon).
    """
    if dt_utc.tzinfo is not None:
        dt_utc = dt_utc.astimezone(timezone.utc).replace(tzinfo=None)

    jd = _julian_day(dt_utc)
    geo = _solar_geometry(jd)
    decl = geo["declination"]
    eq_time = geo["eq_time"]

    # Minutes from UTC midnight of the instant
    minutes_utc = (
        dt_utc.hour * 60.0
        + dt_utc.minute
        + (dt_utc.second + dt_utc.microsecond / 1e6) / 60.0
    )

    # True solar time (minutes). NOAA: tst = mins + eqtime + 4*lon  (lon +E);
    # using UTC time the timezone term is 0.
    true_solar_time = (minutes_utc + eq_time + 4.0 * lon) % 1440.0

    # Hour angle (deg): negative in the morning, positive in the afternoon
    if true_solar_time / 4.0 < 0:
        hour_angle = true_solar_time / 4.0 + 180.0
    else:
        hour_angle = true_solar_time / 4.0 - 180.0

    lat_rad = math.radians(lat)
    decl_rad = math.radians(decl)
    ha_rad = math.radians(hour_angle)

    # Solar zenith angle
    cos_zenith = (
        math.sin(lat_rad) * math.sin(decl_rad)
        + math.cos(lat_rad) * math.cos(decl_rad) * math.cos(ha_rad)
    )
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith = math.degrees(math.acos(cos_zenith))
    elevation_geom = 90.0 - zenith

    # Atmospheric refraction correction (NOAA approximation), in degrees
    refraction = _refraction_correction(elevation_geom)
    elevation = elevation_geom + refraction

    # Azimuth (deg clockwise from north)
    denom = math.cos(lat_rad) * math.sin(math.radians(zenith))
    if abs(denom) < 1e-9:
        # Sun directly overhead / at a pole - azimuth ill-defined; pick 180.
        azimuth = 180.0
    else:
        cos_az = (
            (math.sin(lat_rad) * math.cos(math.radians(zenith))) - math.sin(decl_rad)
        ) / denom
        cos_az = max(-1.0, min(1.0, cos_az))
        az_acos = math.degrees(math.acos(cos_az))
        if hour_angle > 0:
            azimuth = (az_acos + 180.0) % 360.0
        else:
            azimuth = (540.0 - az_acos) % 360.0

    return azimuth, elevation


def _refraction_correction(elevation_geom: float) -> float:
    """NOAA atmospheric refraction correction (degrees) for a geometric
    elevation (degrees)."""
    if elevation_geom > 85.0:
        return 0.0
    te = math.tan(math.radians(elevation_geom))
    if elevation_geom > 5.0:
        corr = 58.1 / te - 0.07 / (te ** 3) + 0.000086 / (te ** 5)
    elif elevation_geom > -0.575:
        corr = (
            1735.0
            + elevation_geom
            * (-518.2 + elevation_geom * (103.4 + elevation_geom * (-12.79 + elevation_geom * 0.711)))
        )
    else:
        corr = -20.772 / te
    return corr / 3600.0  # arc-seconds -> degrees


def sun_position(lat: float, lon: float, dt_local: datetime, tz_offset_hours: float):
    """Sun azimuth & elevation for a *local* naive datetime.

    Parameters
    ----------
    lat, lon : float
        Latitude (+N) and longitude (+E).
    dt_local : datetime
        Naive local clock time at the location.
    tz_offset_hours : float
        Fixed UTC offset in effect at the location/date (e.g. -7, +5.5, +9).

    Returns
    -------
    (azimuth, elevation) : tuple of float - see :func:`sun_position_utc`.
    """
    if dt_local.tzinfo is not None:
        dt_local = dt_local.replace(tzinfo=None)
    dt_utc = dt_local - timedelta(hours=tz_offset_hours)
    return sun_position_utc(lat, lon, dt_utc)


# ---------------------------------------------------------------------------
# Rise/set-style event solving
# ---------------------------------------------------------------------------
def _elevation_at_local_minutes(lat, lon, day: _date, tz_offset_hours, minutes_local):
    """Refraction-corrected sun elevation at ``minutes_local`` minutes after
    local midnight on ``day``."""
    base = datetime(day.year, day.month, day.day)
    dt_local = base + timedelta(minutes=minutes_local)
    _, elev = sun_position(lat, lon, dt_local, tz_offset_hours)
    return elev


def _find_crossing(lat, lon, day, tz_offset_hours, target_angle, morning: bool):
    """Local clock time (minutes after midnight) when the sun's elevation
    crosses ``target_angle``.

    ``morning=True`` finds an ascending crossing (rise / dawn); ``morning=False``
    a descending crossing (set / dusk). Returns minutes-after-midnight (float)
    or ``None`` if no such crossing happens on this calendar day.
    """
    step = 2.0  # minutes
    prev_m = 0.0
    prev_e = _elevation_at_local_minutes(lat, lon, day, tz_offset_hours, prev_m) - target_angle
    m = step
    found = None
    while m <= 1440.0:
        e = _elevation_at_local_minutes(lat, lon, day, tz_offset_hours, m) - target_angle
        ascending = e > prev_e
        crosses = (prev_e <= 0.0 < e) or (prev_e >= 0.0 > e)
        if crosses and ((morning and ascending) or (not morning and not ascending)):
            found = (prev_m, prev_e, m, e)
            break
        prev_m, prev_e = m, e
        m += step

    if found is None:
        return None

    lo_m, lo_e, hi_m, hi_e = found
    # Bisection refinement to ~0.5 second
    for _ in range(40):
        mid_m = 0.5 * (lo_m + hi_m)
        mid_e = _elevation_at_local_minutes(lat, lon, day, tz_offset_hours, mid_m) - target_angle
        if (lo_e <= 0.0 < mid_e) or (lo_e >= 0.0 > mid_e):
            hi_m, hi_e = mid_m, mid_e
        else:
            lo_m, lo_e = mid_m, mid_e
        if abs(hi_m - lo_m) < (0.5 / 60.0):
            break
    return 0.5 * (lo_m + hi_m)


def _minutes_to_time(day: _date, minutes_local):
    """Minutes-after-local-midnight -> naive local datetime, or ``None``."""
    if minutes_local is None:
        return None
    base = datetime(day.year, day.month, day.day)
    return base + timedelta(minutes=minutes_local)


def _coerce_date(d):
    """Accept a date, datetime, or 'YYYY-MM-DD' string and return a date."""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, _date):
        return d
    if isinstance(d, str):
        return datetime.strptime(d, "%Y-%m-%d").date()
    raise TypeError(f"Unsupported date type: {type(d)!r}")


def _solar_noon_minutes(lat, lon, day, tz_offset_hours):
    """Return (minutes_after_midnight, elevation) of the sun's daily maximum."""
    best_m, best_e = 0.0, -999.0
    m = 0.0
    while m <= 1440.0:
        e = _elevation_at_local_minutes(lat, lon, day, tz_offset_hours, m)
        if e > best_e:
            best_e, best_m = e, m
        m += 5.0
    # Golden-section refinement around the peak
    lo = max(0.0, best_m - 5.0)
    hi = min(1440.0, best_m + 5.0)
    for _ in range(60):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        e1 = _elevation_at_local_minutes(lat, lon, day, tz_offset_hours, m1)
        e2 = _elevation_at_local_minutes(lat, lon, day, tz_offset_hours, m2)
        if e1 < e2:
            lo = m1
        else:
            hi = m2
        if hi - lo < (0.25 / 60.0):
            break
    mid = 0.5 * (lo + hi)
    return mid, _elevation_at_local_minutes(lat, lon, day, tz_offset_hours, mid)


# ---------------------------------------------------------------------------
# Public event functions
# ---------------------------------------------------------------------------
def sunrise_sunset(lat, lon, date, tz_offset_hours):
    """Sunrise & sunset local times.

    Returns a dict with naive-local ``datetime`` values (``None`` for polar
    day/night):

        {
          "sunrise": datetime|None,
          "sunset":  datetime|None,
          "solar_noon": datetime,            # sun's highest point that day
          "solar_noon_elevation": float,
          "day_length_hours": float|None,
          "polar_day": bool,                 # sun never sets
          "polar_night": bool,               # sun never rises
        }
    """
    day = _coerce_date(date)
    sr = _find_crossing(lat, lon, day, tz_offset_hours, SUNRISE_SUNSET_ANGLE, morning=True)
    ss = _find_crossing(lat, lon, day, tz_offset_hours, SUNRISE_SUNSET_ANGLE, morning=False)

    noon_minutes, noon_elev = _solar_noon_minutes(lat, lon, day, tz_offset_hours)

    polar_day = sr is None and ss is None and noon_elev > SUNRISE_SUNSET_ANGLE
    polar_night = sr is None and ss is None and noon_elev <= SUNRISE_SUNSET_ANGLE

    sunrise_dt = _minutes_to_time(day, sr)
    sunset_dt = _minutes_to_time(day, ss)
    day_len = None
    if sunrise_dt is not None and sunset_dt is not None:
        day_len = (sunset_dt - sunrise_dt).total_seconds() / 3600.0

    return {
        "sunrise": sunrise_dt,
        "sunset": sunset_dt,
        "solar_noon": _minutes_to_time(day, noon_minutes),
        "solar_noon_elevation": noon_elev,
        "day_length_hours": day_len,
        "polar_day": polar_day,
        "polar_night": polar_night,
    }


def civil_twilight(lat, lon, date, tz_offset_hours):
    """Civil twilight begin/end - the practical "blue hour" boundaries.

    "dawn" is when the sun reaches -6 deg in the morning; "dusk" is when it
    drops below -6 deg in the evening.

        {"dawn": datetime|None, "dusk": datetime|None}

    Morning blue hour ~ [dawn, sunrise]; evening ~ [sunset, dusk].
    """
    day = _coerce_date(date)
    dawn = _find_crossing(lat, lon, day, tz_offset_hours, CIVIL_TWILIGHT_ANGLE, morning=True)
    dusk = _find_crossing(lat, lon, day, tz_offset_hours, CIVIL_TWILIGHT_ANGLE, morning=False)
    return {"dawn": _minutes_to_time(day, dawn), "dusk": _minutes_to_time(day, dusk)}


def golden_hours(lat, lon, date, tz_offset_hours):
    """Morning and evening golden-hour windows.

    Golden hour = sun elevation between ``GOLDEN_LOW_ANGLE`` (-4 deg) and
    ``GOLDEN_HIGH_ANGLE`` (+6 deg). The morning window is the ascending pass
    through that band; the evening window is the descending pass.

        {"morning": (start, end)|None, "evening": (start, end)|None}
    """
    day = _coerce_date(date)
    m_low = _find_crossing(lat, lon, day, tz_offset_hours, GOLDEN_LOW_ANGLE, morning=True)
    m_high = _find_crossing(lat, lon, day, tz_offset_hours, GOLDEN_HIGH_ANGLE, morning=True)
    e_high = _find_crossing(lat, lon, day, tz_offset_hours, GOLDEN_HIGH_ANGLE, morning=False)
    e_low = _find_crossing(lat, lon, day, tz_offset_hours, GOLDEN_LOW_ANGLE, morning=False)

    def _window(a, b):
        # A golden window with an undefined endpoint (one threshold never
        # crossed — midnight-sun / polar-night latitudes) is not a usable
        # shooting window. Collapse it to None so every downstream consumer
        # gets a clean signal instead of a half-open (None, t) tuple.
        if a is None or b is None:
            return None
        return (_minutes_to_time(day, a), _minutes_to_time(day, b))

    return {"morning": _window(m_low, m_high), "evening": _window(e_high, e_low)}


def day_summary(lat, lon, date, tz_offset_hours):
    """Bundle sunrise/sunset, blue hour and golden hours plus a couple of
    derived sun-direction facts for a vlog planner."""
    day = _coerce_date(date)
    ss = sunrise_sunset(lat, lon, day, tz_offset_hours)
    bh = civil_twilight(lat, lon, day, tz_offset_hours)
    gh = golden_hours(lat, lon, day, tz_offset_hours)

    noon_dt = ss["solar_noon"]
    noon_az, noon_el = sun_position(lat, lon, noon_dt, tz_offset_hours)

    sunrise_az = sunset_az = None
    if ss["sunrise"] is not None:
        sunrise_az, _ = sun_position(lat, lon, ss["sunrise"], tz_offset_hours)
    if ss["sunset"] is not None:
        sunset_az, _ = sun_position(lat, lon, ss["sunset"], tz_offset_hours)

    return {
        "date": day.isoformat(),
        "tz_offset_hours": tz_offset_hours,
        "sunrise": ss["sunrise"],
        "sunset": ss["sunset"],
        "solar_noon": noon_dt,
        "solar_noon_elevation": round(noon_el, 3),
        "solar_noon_azimuth": round(noon_az, 3),
        "day_length_hours": ss["day_length_hours"],
        "polar_day": ss["polar_day"],
        "polar_night": ss["polar_night"],
        "blue_hour_dawn": bh["dawn"],
        "blue_hour_dusk": bh["dusk"],
        "golden_hour_morning": gh["morning"],
        "golden_hour_evening": gh["evening"],
        "sunrise_azimuth": None if sunrise_az is None else round(sunrise_az, 2),
        "sunset_azimuth": None if sunset_az is None else round(sunset_az, 2),
    }


# ---------------------------------------------------------------------------
# CLI / self-test
# ---------------------------------------------------------------------------
def _fmt(dt):
    return "--" if dt is None else dt.strftime("%H:%M:%S")


def _print_summary(name, lat, lon, day, tz):
    s = day_summary(lat, lon, day, tz)
    print(f"\n=== {name}  ({lat:.4f}, {lon:.4f})  {day}  UTC{tz:+g} ===")
    print(f"  sunrise        : {_fmt(s['sunrise'])}   azimuth {s['sunrise_azimuth']}")
    print(f"  sunset         : {_fmt(s['sunset'])}   azimuth {s['sunset_azimuth']}")
    print(f"  solar noon     : {_fmt(s['solar_noon'])}   el {s['solar_noon_elevation']}  az {s['solar_noon_azimuth']}")
    print(f"  day length     : {s['day_length_hours']:.3f} h" if s['day_length_hours'] else "  day length     : --")
    print(f"  blue hour dawn : {_fmt(s['blue_hour_dawn'])}")
    print(f"  blue hour dusk : {_fmt(s['blue_hour_dusk'])}")
    gm = s["golden_hour_morning"]
    ge = s["golden_hour_evening"]
    print(f"  golden morning : {_fmt(gm[0]) if gm else '--'} -> {_fmt(gm[1]) if gm else '--'}")
    print(f"  golden evening : {_fmt(ge[0]) if ge else '--'} -> {_fmt(ge[1]) if ge else '--'}")


def _self_test():
    """Run a few reference checks; returns True if all pass."""
    checks = []

    def near(got, exp, tol):
        return got is not None and abs(got - exp) <= tol

    def mins(dt):
        return None if dt is None else dt.hour * 60 + dt.minute + dt.second / 60.0

    # NYC equinox 2025-03-20 (UTC-4): sunrise ~06:58, sunset ~19:08
    ny = sunrise_sunset(40.7128, -74.0060, _date(2025, 3, 20), -4)
    checks.append(("NYC sunrise", near(mins(ny["sunrise"]), 6 * 60 + 58, 3)))
    checks.append(("NYC sunset", near(mins(ny["sunset"]), 19 * 60 + 8, 3)))

    # London solstice 2025-06-21 (UTC+1): sunrise ~04:43, sunset ~21:21
    ld = sunrise_sunset(51.5074, -0.1278, _date(2025, 6, 21), 1)
    checks.append(("London sunrise", near(mins(ld["sunrise"]), 4 * 60 + 43, 4)))
    checks.append(("London sunset", near(mins(ld["sunset"]), 21 * 60 + 21, 4)))

    # SF noon 2026-06-30 (UTC-7): azimuth ~180 (S), elevation ~75.35
    sf = sunrise_sunset(37.7749, -122.4194, _date(2026, 6, 30), -7)
    az, el = sun_position(37.7749, -122.4194, sf["solar_noon"], -7)
    checks.append(("SF noon az~S", near(az, 180.0, 1.0)))
    checks.append(("SF noon el", near(el, 75.35, 1.0)))

    # Boulder 2010-06-21 18:00 UTC: el ~68.8, az ~136.6 (NOAA Solar Calculator)
    az5, el5 = sun_position_utc(39.999, -105.263, datetime(2010, 6, 21, 18, 0, 0))
    checks.append(("Boulder el", near(el5, 68.8, 0.7)))
    checks.append(("Boulder az", near(az5, 136.6, 1.0)))

    ok = all(p for _, p in checks)
    for name, p in checks:
        print(f"  [{'PASS' if p else 'FAIL'}] {name}")
    print(f"self-test: {sum(p for _, p in checks)}/{len(checks)} passed")
    return ok


def _hm(dt):
    return None if dt is None else dt.strftime("%H:%M")


def day_summary_json(lat, lon, date, tz_offset_hours):
    """JSON-serialisable version of day_summary (times as 'HH:MM' strings)."""
    s = day_summary(lat, lon, date, tz_offset_hours)
    gm = s["golden_hour_morning"]
    ge = s["golden_hour_evening"]
    return {
        "date": str(_coerce_date(date)),
        "tz_offset_hours": tz_offset_hours,
        "sunrise": _hm(s["sunrise"]),
        "sunset": _hm(s["sunset"]),
        "solar_noon": _hm(s["solar_noon"]),
        "solar_noon_elevation": s["solar_noon_elevation"],
        "day_length_hours": round(s["day_length_hours"], 2) if s["day_length_hours"] else None,
        "polar_day": s["polar_day"],
        "polar_night": s["polar_night"],
        "blue_hour_dawn": _hm(s["blue_hour_dawn"]),
        "blue_hour_dusk": _hm(s["blue_hour_dusk"]),
        "golden_hour_morning": [_hm(gm[0]), _hm(gm[1])] if gm else None,
        "golden_hour_evening": [_hm(ge[0]), _hm(ge[1])] if ge else None,
        "sunrise_azimuth": s["sunrise_azimuth"],
        "sunset_azimuth": s["sunset_azimuth"],
    }


def azimuth_to_compass(az):
    """Turn an azimuth in degrees into a Chinese compass label, e.g. 东南."""
    if az is None:
        return None
    dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    return dirs[int((az + 22.5) % 360 // 45)]


if __name__ == "__main__":
    import argparse
    import json as _json

    p = argparse.ArgumentParser(description="Sunrise/sunset, golden & blue hour, sun azimuth — pure NOAA math, no network.")
    p.add_argument("--lat", type=float, help="latitude (+N)")
    p.add_argument("--lon", type=float, help="longitude (+E)")
    p.add_argument("--date", help="YYYY-MM-DD")
    p.add_argument("--tz", type=float, default=8.0, help="UTC offset hours in effect that day (default +8 China)")
    p.add_argument("--at", help="optional local HH:MM — also print the sun's azimuth/elevation at this time")
    p.add_argument("--selftest", action="store_true", help="run reference checks and exit")
    args = p.parse_args()

    if args.selftest:
        raise SystemExit(0 if _self_test() else 1)

    if args.lat is None or args.lon is None or not args.date:
        p.error("--lat, --lon and --date are required (or use --selftest)")

    out = day_summary_json(args.lat, args.lon, args.date, args.tz)
    out["sunrise_direction"] = azimuth_to_compass(out["sunrise_azimuth"])
    out["sunset_direction"] = azimuth_to_compass(out["sunset_azimuth"])
    if args.at:
        hh, mm = (int(x) for x in args.at.split(":"))
        dt_local = datetime.combine(_coerce_date(args.date), datetime.min.time()).replace(hour=hh, minute=mm)
        az, el = sun_position(args.lat, args.lon, dt_local, args.tz)
        out["at"] = {"time": args.at, "azimuth": round(az, 1), "elevation": round(el, 1),
                     "direction": azimuth_to_compass(az)}
    print(_json.dumps(out, ensure_ascii=False, indent=2))