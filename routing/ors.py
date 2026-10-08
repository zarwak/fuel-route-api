"""Thin client for OpenRouteService (free tier): geocoding and driving routes."""
import hashlib

import requests
from django.conf import settings
from django.core.cache import cache

BASE = "https://api.openrouteservice.org"
METERS_PER_MILE = 1609.344
TIMEOUT = 30


class ORSError(Exception):
    """The routing service failed or is not configured."""


class NoRoute(Exception):
    """The places exist but no driving route connects them (or one is off the road network)."""


class PlaceNotFound(Exception):
    """Geocoding found no US place for the text."""


def _headers():
    if not settings.ORS_API_KEY:
        raise ORSError("ORS_API_KEY is not set (see .env.example)")
    return {"Authorization": settings.ORS_API_KEY}


def _ors_error_code(response):
    try:
        return response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def geocode(text, calls):
    """'Chicago, IL' -> {'lat', 'lon', 'label'}. Cached for a day; counts real calls."""
    key = "geo:" + hashlib.md5(" ".join(text.lower().split()).encode()).hexdigest()
    hit = cache.get(key)
    if hit:
        return hit
    try:
        r = requests.get(
            f"{BASE}/geocode/search",
            headers=_headers(),
            params={"text": text, "boundary.country": "US", "size": 1},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise ORSError(f"Geocoding request failed: {e}") from e
    calls.append("geocode")
    if r.status_code != 200:
        raise ORSError(f"Geocoding failed (HTTP {r.status_code})")
    features = r.json().get("features") or []
    if not features:
        raise PlaceNotFound(f"No US place found for '{text}'")
    lon, lat = features[0]["geometry"]["coordinates"]
    place = {"lat": lat, "lon": lon, "label": features[0]["properties"].get("label", text),
             "source": "openrouteservice"}
    cache.set(key, place, 86400)
    return place


def drive_route(start, finish, calls):
    """One directions call. Returns {'coords': [[lon, lat], ...], 'miles', 'hours'}. Cached for an hour."""
    key = "route:{:.4f},{:.4f}|{:.4f},{:.4f}".format(start["lat"], start["lon"], finish["lat"], finish["lon"])
    hit = cache.get(key)
    if hit:
        return hit
    try:
        r = requests.post(
            f"{BASE}/v2/directions/driving-car/geojson",
            headers=_headers(),
            json={
                "coordinates": [[start["lon"], start["lat"]], [finish["lon"], finish["lat"]]],
                "radiuses": [-1, -1],  # snap to the nearest road however far
                "instructions": False,  # we only need the line, not turn-by-turn
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise ORSError(f"Routing request failed: {e}") from e
    calls.append("route")
    if r.status_code == 404 and _ors_error_code(r) in (2009, 2010):  # no route / no road near a point
        raise NoRoute("No driving route found between these places. One may be off the road network "
                      "or not connected by road; try a more specific address or coordinates on a road.")
    if r.status_code != 200:
        raise ORSError(f"Routing failed (HTTP {r.status_code}): {r.text[:200]}")
    feature = r.json()["features"][0]
    summary = feature["properties"]["summary"]
    result = {
        "coords": feature["geometry"]["coordinates"],
        "miles": summary["distance"] / METERS_PER_MILE,
        "hours": summary["duration"] / 3600,
    }
    cache.set(key, result, 3600)
    return result
