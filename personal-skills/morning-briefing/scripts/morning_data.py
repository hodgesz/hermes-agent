#!/usr/bin/env python3
"""Gather local, stdlib-only data for the morning-briefing skill.

Produces a JSON blob the agent uses to narrate a concise briefing. The agent
is responsible for headline selection / summarization — this script only
collects facts that don't need a model.

Currently gathers:
  - Weather from wttr.in (JSON endpoint) for a configurable location
  - Current date/time with day-of-week and weekday/weekend flag
  - A "market hours" hint (NYSE open/closed) so the agent knows whether to
    fetch market data

Usage::

    python morning_data.py                         # defaults (Denver, imperial)
    python morning_data.py --location "Denver"
    python morning_data.py --location "94103" --units metric
    python morning_data.py --timeout 8

Exit codes: 0 on success (even with partial data), 1 on argument error.

The script never raises on network issues — it returns a JSON payload with
``weather.error`` populated so the agent can fall back to web_search.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


WTTR_URL = "https://wttr.in/{location}?format=j1"
USER_AGENT = "HermesAgent/1.0 (morning-briefing skill)"
DEFAULT_LOCATION = "Denver"
DEFAULT_UNITS = "imperial"   # "imperial" | "metric"


def _http_get_json(url: str, timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — user-provided URL, stdlib only
        data = resp.read()
    return json.loads(data.decode("utf-8"))


def _fetch_weather(location: str, units: str, timeout: float) -> Dict[str, Any]:
    url = WTTR_URL.format(location=urllib.parse.quote(location))
    try:
        raw = _http_get_json(url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"error": f"weather fetch failed: {exc}", "source": url}

    current = (raw.get("current_condition") or [{}])[0]
    today = (raw.get("weather") or [{}])[0]
    nearest = (raw.get("nearest_area") or [{}])[0]

    def _int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    if units == "imperial":
        temp = _int(current.get("temp_F"))
        high = _int(today.get("maxtempF"))
        low = _int(today.get("mintempF"))
        unit_label = "°F"
    else:
        temp = _int(current.get("temp_C"))
        high = _int(today.get("maxtempC"))
        low = _int(today.get("mintempC"))
        unit_label = "°C"

    hourly = today.get("hourly") or []
    precip_chance = 0
    for block in hourly:
        try:
            precip_chance = max(precip_chance, int(block.get("chanceofrain", 0)))
        except (TypeError, ValueError):
            continue

    condition_desc = ""
    weather_desc = current.get("weatherDesc") or []
    if isinstance(weather_desc, list) and weather_desc:
        condition_desc = str(weather_desc[0].get("value", "")).strip()

    area_name = ""
    area_val = nearest.get("areaName") or []
    if isinstance(area_val, list) and area_val:
        area_name = str(area_val[0].get("value", "")).strip()

    return {
        "location": area_name or location,
        "temperature": temp,
        "unit": unit_label,
        "condition": condition_desc,
        "high": high,
        "low": low,
        "precip_chance": precip_chance,
        "source": "wttr.in",
    }


def _date_context(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    now = now or _dt.datetime.now().astimezone()
    weekday = now.strftime("%A")
    is_weekend = now.weekday() >= 5
    return {
        "iso": now.date().isoformat(),
        "weekday": weekday,
        "is_weekend": is_weekend,
        "local_time": now.strftime("%H:%M"),
        "tz": now.tzname() or "",
    }


def _market_hint(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Rough NYSE open-hours hint.

    This is not a real trading-calendar — it just tells the agent whether to
    bother fetching market data. Holidays still slip through.
    """
    now = now or _dt.datetime.now().astimezone()
    is_weekday = now.weekday() < 5
    hour = now.hour
    likely_open = is_weekday and 9 <= hour < 17
    return {
        "likely_open": likely_open,
        "note": "rough NYSE window, ignores holidays",
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Gather local data for the morning briefing.")
    parser.add_argument("--location", default=DEFAULT_LOCATION, help="City, zip, or wttr.in-compatible string")
    parser.add_argument("--units", choices=["imperial", "metric"], default=DEFAULT_UNITS)
    parser.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout in seconds")
    parser.add_argument("--no-weather", action="store_true", help="Skip weather fetch (local only)")
    args = parser.parse_args(argv)

    payload: Dict[str, Any] = {
        "date": _date_context(),
        "market_hint": _market_hint(),
    }

    if args.no_weather:
        payload["weather"] = {"skipped": True}
    else:
        payload["weather"] = _fetch_weather(args.location, args.units, args.timeout)

    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
