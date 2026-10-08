"""Pick the cheapest fuel stops along a route.

Pure logic: no database, no HTTP. A station is just (id, mile_marker, price),
where mile_marker is how far along the route it sits.

Rules: the tank starts full (that first tank is free, it was bought before the
trip); the car can drive `range_miles` on a full tank; we only pay for fuel
bought at stops, and never buy more than the strategy needs.
"""
from dataclasses import dataclass


class NoFeasibleRoute(Exception):
    """A gap between reachable stations is longer than the vehicle's range.

    Carries the partial plan made before the gap: `stops` and `cost` (including a final
    top-up at the last station, the best that can be done), and `mile` where we got stuck.
    """

    def __init__(self, message, stops=(), cost=0.0, mile=0.0):
        super().__init__(message)
        self.stops, self.cost, self.mile = list(stops), cost, mile


@dataclass(frozen=True)
class Stop:
    station_id: int
    mile: float
    price: float  # $/gallon
    gallons: float  # bought here
    cost: float


def plan_stops(stations, total_miles, range_miles=500.0, mpg=10.0):
    """Greedy plan. Returns (stops, total_cost).

    At each point: if a cheaper station is reachable on a full tank, buy only
    enough to reach it; otherwise fill up and head for the cheapest reachable
    station (or buy just enough to finish if the destination is in reach).
    """
    # Stations sharing a mile marker (e.g. same city) are one place: only the cheapest can ever matter.
    cheapest_at = {}
    for s in stations:
        if 0 < s[1] < total_miles and (s[1] not in cheapest_at or s[2] < cheapest_at[s[1]][2]):
            cheapest_at[s[1]] = s
    ahead = sorted(cheapest_at.values(), key=lambda s: s[1])
    pos, fuel = 0.0, range_miles  # fuel is measured in miles of range left
    price = 0.0  # the starting tank is already paid for
    stops, total = [], 0.0
    here = None  # station we are standing at (None = start)

    while True:
        if total_miles - pos <= range_miles:
            reach = [s for s in ahead if pos < s[1] < total_miles]
        else:
            reach = [s for s in ahead if pos < s[1] <= pos + range_miles]
        cheaper = next((s for s in reach if s[2] < price), None)

        if cheaper is not None:
            target, target_miles = cheaper, cheaper[1] - pos
            need = max(0.0, target_miles - fuel)
        elif total_miles - pos <= range_miles:
            target, target_miles = None, total_miles - pos
            need = max(0.0, target_miles - fuel)
        elif reach:
            target = min(reach, key=lambda s: (s[2], s[1]))
            target_miles = target[1] - pos
            need = range_miles - fuel  # fill up
        else:
            if here is not None and fuel < range_miles:  # nothing ahead: top up at the last station
                gallons = (range_miles - fuel) / mpg
                total += gallons * price
                stops.append(Stop(here[0], here[1], price, gallons, gallons * price))
            raise NoFeasibleRoute(
                f"No station within {range_miles:.0f} miles after mile {pos:.0f}", merge_stops(stops), total, pos
            )

        if need > 0:
            gallons = need / mpg
            cost = gallons * price
            total += cost
            stops.append(Stop(here[0], here[1], price, gallons, cost))
            fuel += need
        if target is None:
            break
        fuel -= target_miles
        pos, price, here = target[1], target[2], target
        ahead = [s for s in ahead if s[1] > pos]

    return merge_stops(stops), total


def merge_stops(stops):
    """Fold repeated buys at the same station into one entry."""
    merged = {}
    for s in stops:
        if s.station_id in merged:
            m = merged[s.station_id]
            merged[s.station_id] = Stop(m.station_id, m.mile, m.price, m.gallons + s.gallons, m.cost + s.cost)
        else:
            merged[s.station_id] = s
    return list(merged.values())
