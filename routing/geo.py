"""Geometry helpers: distances and 'which stations are near the route'."""
import math
from collections import defaultdict

EARTH_MILES = 3958.8
MILES_PER_DEGREE = EARTH_MILES * math.pi / 180  # one degree of latitude, about 69.09 miles


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles (exact; used where accuracy matters)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_MILES * math.asin(math.sqrt(a))


def mile_markers(coords, total_miles):
    """Miles travelled at each route point, scaled to match the routing service's own total.

    Route points are a fraction of a mile apart, so a flat-earth approximation per segment is
    accurate to well under 0.1% and several times cheaper than haversine.
    """
    cum = [0.0]
    running = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        dy = (lat2 - lat1) * MILES_PER_DEGREE
        dx = (lon2 - lon1) * MILES_PER_DEGREE * math.cos(math.radians((lat1 + lat2) / 2))
        running += math.hypot(dx, dy)
        cum.append(running)
    scale = total_miles / running if running else 1.0
    return [c * scale for c in cum]


def stations_along_route(stations, coords, markers, max_miles):
    """[(station, mile_marker, miles_off_route)] for stations within max_miles of the route.

    Route points go into a coarse grid so each station only checks nearby points instead of all
    ~5,000. Candidates are ranked with a cheap flat-earth distance; the exact haversine distance
    is computed once per station, for its nearest point only.
    """
    cell = max_miles / 40.0  # degrees; wider than max_miles even at 50N
    grid = defaultdict(list)
    for i, (lon, lat) in enumerate(coords):
        grid[(int(lat // cell), int(lon // cell))].append(i)

    found = []
    for s in stations:
        ci, cj = int(s.lat // cell), int(s.lon // cell)
        shrink = math.cos(math.radians(s.lat))  # a degree of longitude is shorter away from the equator
        best_d2, best_i = None, None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for i in grid.get((ci + di, cj + dj), ()):
                    dy = s.lat - coords[i][1]
                    dx = (s.lon - coords[i][0]) * shrink
                    d2 = dy * dy + dx * dx
                    if best_d2 is None or d2 < best_d2:
                        best_d2, best_i = d2, i
        if best_i is not None:
            d = haversine(s.lat, s.lon, coords[best_i][1], coords[best_i][0])
            if d <= max_miles:
                found.append((s, markers[best_i], d))
    return found
