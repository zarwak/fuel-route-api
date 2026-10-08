# Fuel Route API

A Django API that takes a start and finish location in the USA and returns:

- the **driving route** as GeoJSON (ready to draw on a map),
- the **cheapest places to fuel up** along it, given a **500-mile range**,
- the **total money spent on fuel**, at **10 miles per gallon**, using the supplied fuel price file.

Built with Django 6.1, OpenRouteService (free tier) for routing, and SQLite.

## Quick start

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate        macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then put your free OpenRouteService key in .env
python manage.py migrate
python manage.py load_stations   # one-time: loads ~6,400 stations and ~48,000 US places (for offline city lookup)
python manage.py runserver
```

Get a free key at <https://openrouteservice.org/dev/#/signup> and set `ORS_API_KEY=` in `.env`.

A ready-made Postman collection is in `postman/` (import it, then run the requests in order).

Run the tests (no key or internet needed, the routing service is faked):

```bash
python manage.py test routing
```

## Map page

Open <http://127.0.0.1:8000/> for a small demo page: type a start and finish, and it draws the route and numbered fuel-stop pins on a Leaflet map, with the cost summary. It simply calls the API below. (Map tiles load from Esri's free public street basemap, so it needs internet.)

## The endpoint

`POST /api/route` (`GET /api/route?start=...&finish=...` also works, handy in a browser)

```json
{ "start": "Chicago, IL", "finish": "Dallas, TX" }
```

`start` and `finish` each accept any of:

| Form | Example |
|---|---|
| Place name | `"Chicago, IL"` or `"Chicago, Illinois"` (resolved offline, no API call), or a street address / ZIP code (geocoded) |
| `"lat,lon"` text | `"41.8781,-87.6298"` |
| Object | `{"lat": 41.8781, "lon": -87.6298}` |

### Response (Chicago → Dallas, trimmed)

```json
{
  "start":  {"lat": 41.83, "lon": -87.68, "label": "Chicago, IL, USA", "source": "census-offline"},
  "finish": {"lat": 32.79, "lon": -96.77, "label": "Dallas, TX, USA", "source": "census-offline"},
  "distance_miles": 971.2,
  "duration_hours": 15.5,
  "gallons_used": 97.12,
  "total_fuel_cost": 136.0,
  "currency": "USD",
  "fuel_stops": [
    {
      "name": "HUCKS FOOD & FUEL #379", "address": "I-57, EXIT 53",
      "city": "Marion", "state": "IL", "lat": 37.734445, "lon": -88.941164,
      "mile_marker": 318.0, "miles_off_route": 1.0,
      "price_per_gallon": 2.929, "gallons_to_buy": 29.37, "cost": 86.03
    }
  ],
  "map": { "type": "FeatureCollection", "features": ["route LineString", "start", "finish", "fuel stops ..."] },
  "meta": {
    "routing_service_calls": 1,
    "calls": ["route"],
    "stations_searched_within_miles": 5,
    "assumptions": ["..."]
  }
}
```

| Field | Meaning |
|---|---|
| `fuel_stops` | Where to buy, how much, at what price, in route order. |
| `total_fuel_cost` | Dollars spent at the fuel stops (see assumptions). |
| `gallons_used` | Whole-trip consumption: `distance_miles / 10`. |
| `plan_complete` / `warning` | `true` / `null` normally. If the price data has a 500+ mile stretch with no station, the route is still returned with the stops planned up to that gap, `plan_complete: false`, and a `warning` explaining it. |
| `map` | A GeoJSON `FeatureCollection`: the route line, start, finish and each stop (`properties.kind` tells them apart). Paste it into <https://geojson.io> to see it. |
| `meta.routing_service_calls` | How many real calls were made to the routing service for this request. |

### Errors

| Status | When |
|---|---|
| 400 | Missing or malformed `start`/`finish`, a place name over 200 characters, invalid JSON, or a location outside the USA |
| 404 | A place name could not be found in the USA |
| 422 | No driving route connects the places (e.g. Hawaii to California) |
| 502 | The routing service failed or `ORS_API_KEY` is not set |

All errors are `{"error": "message"}`.

## How it works

1. **Resolve places.** `"City, ST"` names are looked up in a local table built from the free US Census Gazetteer (no API call). Coordinates need no lookup. Only street addresses, ZIP codes and unknown names use the geocoding API. If a city's centre point turns out to be too far from any road to route from, that place is re-resolved with the geocoder and the route retried once.
2. **One routing call** to OpenRouteService returns the whole road geometry and distance.
3. **Find candidate stations** with one indexed database query (the route's bounding box), then keep stations within **5 miles** of the route. Route points go into a coarse grid, so each station checks only nearby points instead of all ~5,000.
4. **Plan the stops** (`routing/planner.py`), a greedy strategy:
   - If a **cheaper station is reachable** on a full tank, buy only enough to get there.
   - If **nothing cheaper is in reach**, fill the tank and head for the cheapest reachable station.
   - If the **destination is in reach**, buy just enough to finish.
5. **Price it:** gallons bought at each stop × that stop's price.

### Speed and call budget

| Request | Routing-service calls |
|---|---|
| Coordinates, or `"City, ST"` names | **1** |
| Street address, ZIP code or unknown name | 2 (one geocode + one route), or 3 if both need geocoding |
| Repeat of a recent request | **0** (cached) |

Our own work (database query, finding stations near the route, planning, building the JSON) takes roughly **0.05 to 0.15 seconds**, measured on repeat requests for 400 to 1,500-mile routes. The routing call is the slow part (about 1.5 to 6 seconds on the free tier, longer for longer routes, and outside our control). Repeats of a recent route skip it entirely, so they return in about 0.1 seconds.

## Data preparation (`load_stations`)

The supplied price file has **no coordinates** (addresses are highway exits like `I-57, EXIT 53`). Geocoding about 6,700 of them through a free API would be slow, rate-limited and inaccurate, so:

- stations are placed at their **city and state**, using the free US Census Gazetteer files in `data/` (offline, no API calls);
- duplicate rows for the same station keep the **lowest** price (about 600 stations had conflicting prices);
- Canadian rows are dropped (the brief is USA only).

Result: **6,442 stations** loaded; **184 (2.8%)** skipped because their city name could not be matched to the Census data.

## Assumptions and limitations

- **Full tank at the start.** That first tank is not counted in `total_fuel_cost` (it was bought before the trip), so a trip under 500 miles costs $0. `gallons_used` shows the full consumption so the two can be compared.
- **Station location is city-level**, not street-level. Usually within a few miles, which is why "near the route" means within 5 miles. If no workable plan exists at 5 miles, the search widens to 15 and then 30 miles (reported in `meta.stations_searched_within_miles`).
- **Detours are not added** to distance or fuel; a station off the road counts as if it were on the route.
- **No safety reserve**: the plan may arrive at a station with an almost-empty tank. A reserve would be a one-line change to the planner.
- The greedy strategy is the standard approach for the fuel-stop problem, and the tests check it against hand-worked examples.
- Prices are as supplied in the CSV; there is no live price lookup.
- **The CSV is thin on the West Coast** (only 8 stations in California, 29 in Oregon, versus 776 in Texas). A long trip through that region can have a 500-mile stretch with no listed station; the API then returns the route and a partial plan with `plan_complete: false` and a `warning` (Seattle to San Diego does this) instead of failing.
- **Offline city points are Census "internal points"**, so a route may start a few miles from where a geocoder would place the city centre; distance and cost can differ slightly (under 1%) depending on how a place is specified.
- Place names are geocoded within the USA only, so a non-US name resolves to the closest US match ("Zurich, Switzerland" becomes Lake Zurich, IL). The resolved place is always returned in `start` and `finish`, so it is visible.

## Project layout

```
config/                 Django settings and URLs
routing/
  models.py             Station (price, lat, lon; indexed) and Place (US cities for offline lookup)
  places.py             offline "City, ST" lookup
  ors.py                OpenRouteService client (geocode + route, cached)
  geo.py                distance maths and "stations near the route" search
  planner.py            pure fuel-stop planning logic (no DB, no HTTP)
  views.py              POST/GET /api/route, and the map page at /
  templates/routing/map.html   the Leaflet demo page (one self-contained file)
  management/commands/load_stations.py   one-time data cleaning and loading
  tests.py, test_api.py planner, geometry, client and API tests
data/                   price CSV + US Census Gazetteer files (public domain)
```
