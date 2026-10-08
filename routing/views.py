import json
import re

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import ors, places
from .geo import mile_markers, stations_along_route
from .models import Station
from .planner import NoFeasibleRoute, plan_stops

LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
# Rough box around the US incl. Alaska/Hawaii; the geocoder is also restricted to US.
US_LAT, US_LON = (17.0, 72.0), (-180.0, -64.0)


MAX_PLACE_CHARS = 200


class BadRequest(Exception):
    pass


def _error(status, message):
    return JsonResponse({"error": message}, status=status)


def resolve_place(value, field, calls):
    """Accept 'Chicago, IL', '41.88,-87.66' or {'lat': .., 'lon': ..}."""
    if isinstance(value, dict):
        try:
            lat, lon = float(value["lat"]), float(value["lon"])
        except (KeyError, TypeError, ValueError):
            raise BadRequest(f"'{field}' object needs numeric 'lat' and 'lon'")
    elif isinstance(value, str) and value.strip():
        if len(value) > MAX_PLACE_CHARS:
            raise BadRequest(f"'{field}' is too long (max {MAX_PLACE_CHARS} characters)")
        m = LATLON.match(value)
        if not m:
            return places.lookup(value) or ors.geocode(value, calls)
        lat, lon = float(m.group(1)), float(m.group(2))
    else:
        raise BadRequest(f"'{field}' is required: a place name, 'lat,lon', or {{'lat': .., 'lon': ..}}")
    if not (US_LAT[0] <= lat <= US_LAT[1] and US_LON[0] <= lon <= US_LON[1]):
        raise BadRequest(f"'{field}' is outside the USA")
    return {"lat": lat, "lon": lon, "label": f"{lat:.4f}, {lon:.4f}", "source": "coordinates"}


def _regeocode(place, calls):
    return ors.geocode(place["query"], calls) if place.get("source") == "census-offline" else place


def _public(place):
    return {k: place[k] for k in ("lat", "lon", "label", "source") if k in place}


def _point(place, kind):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [place["lon"], place["lat"]]},
        "properties": {"kind": kind, "label": place["label"]},
    }


@csrf_exempt
@require_http_methods(["GET", "POST"])
def route_view(request):
    """Plan a route and the cheapest fuel stops. See README for the contract."""
    if request.method == "POST":
        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return _error(400, "Body must be valid JSON")
        if not isinstance(body, dict):
            return _error(400, "Body must be a JSON object with 'start' and 'finish'")
    else:
        body = request.GET

    calls = []  # every real call to the routing service, reported back for transparency
    try:
        start = resolve_place(body.get("start"), "start", calls)
        finish = resolve_place(body.get("finish"), "finish", calls)
        try:
            route = ors.drive_route(start, finish, calls)
        except ors.NoRoute:
            # A city's offline point can sit away from any road. Re-resolve those places with the
            # geocoder (which snaps to roads better) and retry once.
            fixed = [_regeocode(p, calls) for p in (start, finish)]
            if fixed == [start, finish]:
                raise
            start, finish = fixed
            route = ors.drive_route(start, finish, calls)
    except BadRequest as e:
        return _error(400, str(e))
    except ors.PlaceNotFound as e:
        return _error(404, str(e))
    except ors.NoRoute as e:
        return _error(422, str(e))
    except ors.ORSError as e:
        return _error(502, str(e))

    coords, total_miles = route["coords"], route["miles"]
    markers = mile_markers(coords, total_miles)

    # One cheap database query: stations inside the route's bounding box (+ widest search margin).
    lats = [c[1] for c in coords]
    lons = [c[0] for c in coords]
    widest = max(settings.STATION_SEARCH_MILES)
    pad_lat, pad_lon = widest / 60.0, widest / 40.0
    candidates = list(
        Station.objects.filter(
            lat__range=(min(lats) - pad_lat, max(lats) + pad_lat),
            lon__range=(min(lons) - pad_lon, max(lons) + pad_lon),
        )
    )

    plan, partials = None, []
    for tolerance in settings.STATION_SEARCH_MILES:
        near = stations_along_route(candidates, coords, markers, tolerance)
        by_id = {s.opis_id: (s, off) for s, _, off in near}
        try:
            stops, total_cost = plan_stops(
                [(s.opis_id, mile, float(s.price)) for s, mile, _ in near],
                total_miles,
                settings.VEHICLE_RANGE_MILES,
                settings.VEHICLE_MPG,
            )
        except NoFeasibleRoute as e:
            partials.append((e.mile, -tolerance, tolerance, e, by_id))
            continue
        plan = (tolerance, stops, total_cost, by_id, None)
        break
    if plan is None:
        # The price data has a stretch with no usable station. Still return the route, and the
        # plan up to the gap (the attempt that got furthest), with a clear warning.
        mile, _, tolerance, e, by_id = max(partials, key=lambda p: (p[0], p[1]))
        warning = (
            f"No fuel station in the price data within {settings.VEHICLE_RANGE_MILES} miles after mile {mile:.0f} "
            f"(searched up to {widest} miles off the route), so the full trip cannot be planned. "
            "fuel_stops and total_fuel_cost cover only the route up to that point."
        )
        plan = (tolerance, e.stops, e.cost, by_id, warning)

    tolerance, stops, total_cost, by_id, warning = plan
    fuel_stops, stop_features = [], []
    for stop in stops:
        station, off = by_id[stop.station_id]
        item = {
            "name": station.name,
            "address": station.address,
            "city": station.city,
            "state": station.state,
            "lat": station.lat,
            "lon": station.lon,
            "mile_marker": round(stop.mile, 1),
            "miles_off_route": round(off, 1),
            "price_per_gallon": round(stop.price, 3),
            "gallons_to_buy": round(stop.gallons, 2),
            "cost": round(stop.cost, 2),
        }
        fuel_stops.append(item)
        stop_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [station.lon, station.lat]},
                "properties": {"kind": "fuel_stop", **item},
            }
        )

    route_feature = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"kind": "route"},
    }
    return JsonResponse(
        {
            "start": _public(start),
            "finish": _public(finish),
            "distance_miles": round(total_miles, 1),
            "duration_hours": round(route["hours"], 1),
            "gallons_used": round(total_miles / settings.VEHICLE_MPG, 2),
            "total_fuel_cost": round(total_cost, 2),
            "currency": "USD",
            "plan_complete": warning is None,
            "warning": warning,
            "fuel_stops": fuel_stops,
            "map": {
                "type": "FeatureCollection",
                "features": [route_feature, _point(start, "start"), _point(finish, "finish"), *stop_features],
            },
            "meta": {
                "routing_service_calls": len(calls),
                "calls": calls,
                "stations_searched_within_miles": tolerance,
                "assumptions": [
                    f"Vehicle starts with a full tank ({settings.VEHICLE_RANGE_MILES}-mile range, "
                    f"{settings.VEHICLE_MPG} mpg); that first tank is not counted in total_fuel_cost.",
                    "Stations are located by city (offline), not by exact street address.",
                ],
            },
        }
    )


def map_page(request):
    """A small demo page that calls /api/route and draws the result with Leaflet."""
    return render(request, "routing/map.html")
