"""The planner must match an exact (dynamic-programming) solver on random trips.

Units are simplified so the solver is exact: whole miles, mpg=1 (1 mile = 1 unit of fuel).
"""
import random

from django.test import SimpleTestCase

from .planner import NoFeasibleRoute, plan_stops


def optimal_cost(stations, total, tank):
    """Cheapest possible cost with a free full starting tank, or None if the trip is impossible."""
    stops = sorted((s for s in stations if 0 < s[1] < total), key=lambda s: s[1])
    miles = [0] + [s[1] for s in stops] + [total]
    price = [None] + [s[2] for s in stops] + [None]
    inf = float("inf")
    best = [inf] * (tank + 1)
    best[tank] = 0.0
    for i in range(len(miles) - 1):
        gap = miles[i + 1] - miles[i]
        nxt = [inf] * (tank + 1)
        for fuel in range(tank + 1):
            if best[fuel] == inf:
                continue
            most = tank if price[i] is not None else fuel  # only stations sell fuel
            for after in range(fuel, most + 1):
                cost = best[fuel] + (after - fuel) * (price[i] or 0)
                if after >= gap and cost < nxt[after - gap]:
                    nxt[after - gap] = cost
        best = nxt
    result = min(best)
    return None if result == inf else result


class PlannerOptimalityTests(SimpleTestCase):
    def test_matches_exact_solver_on_random_trips(self):
        rng = random.Random(2026)
        compared = 0
        for _ in range(1500):
            tank, total = rng.randint(5, 30), rng.randint(1, 140)
            price = rng.choice([lambda: rng.randint(1, 9), lambda: rng.randint(1, 3), lambda: rng.randint(1, 50)])
            stations = [(i, rng.randint(0, total + 1), price()) for i in range(rng.randint(0, 16))]
            want = optimal_cost(stations, total, tank)
            try:
                _, got = plan_stops(stations, total, range_miles=tank, mpg=1)
            except NoFeasibleRoute:
                got = None
            self.assertEqual(want is None, got is None, (tank, total, stations))
            if want is not None:
                self.assertAlmostEqual(got, want, places=6, msg=(tank, total, stations))
                compared += 1
        self.assertGreater(compared, 300)  # guard: the random trips really exercised the planner

    def test_stations_at_the_same_mile_use_the_cheapest(self):
        # Regression: two stations in one city share a mile marker. Visiting the pricey one
        # used to hide the cheap one, so the plan bought the whole tank at $15 instead of $2.
        stations = [(1, 8, 8), (2, 9, 25), (10, 9, 24), (7, 14, 5), (8, 23, 27), (6, 28, 19),
                    (0, 36, 15), (4, 36, 2), (11, 38, 27), (3, 42, 24)]
        _, cost = plan_stops(stations, 44, range_miles=21, mpg=1)
        self.assertAlmostEqual(cost, 105.0)
