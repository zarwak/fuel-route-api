from django.test import SimpleTestCase

from .planner import NoFeasibleRoute, plan_stops


class PlannerTests(SimpleTestCase):
    def test_short_trip_needs_no_stops(self):
        stops, cost = plan_stops([(1, 100, 3.0)], total_miles=300)
        self.assertEqual((stops, cost), ([], 0.0))

    def test_skips_pricey_station_for_cheaper_one(self):
        # 800 mi. Stations: mile 400 @ $4.00, mile 450 @ $3.00.
        # Start full (500). Reach mile 450 with 50 left, need 350 -> buy 300 mi = 30 gal @ $3.
        stops, cost = plan_stops([(1, 400, 4.0), (2, 450, 3.0)], total_miles=800)
        self.assertEqual([s.station_id for s in stops], [2])
        self.assertAlmostEqual(cost, 30 * 3.0)

    def test_fills_up_at_cheap_station_when_next_is_pricier(self):
        # 1000 mi. Mile 300 @ $3, mile 600 @ $4.
        # At 300 (200 left): nothing cheaper ahead -> fill 30 gal @ $3.
        # At 600 (200 left): 400 to go -> buy 20 gal @ $4.
        stops, cost = plan_stops([(1, 300, 3.0), (2, 600, 4.0)], total_miles=1000)
        self.assertEqual([(s.station_id, round(s.gallons, 6)) for s in stops], [(1, 30.0), (2, 20.0)])
        self.assertAlmostEqual(cost, 30 * 3.0 + 20 * 4.0)

    def test_gallons_bought_equal_miles_beyond_free_tank(self):
        stops, _ = plan_stops([(1, 300, 3.0), (2, 600, 4.0), (3, 900, 3.5)], total_miles=1300)
        self.assertAlmostEqual(sum(s.gallons for s in stops), (1300 - 500) / 10.0)

    def test_gap_error_carries_the_partial_plan(self):
        # Station at mile 300 is the last one; 600 miles remain, so the trip is impossible.
        # The partial plan tops up there (200 miles left -> 30 gallons) before giving up.
        with self.assertRaises(NoFeasibleRoute) as ctx:
            plan_stops([(1, 300, 3.0)], total_miles=900)
        e = ctx.exception
        self.assertEqual(e.mile, 300)
        self.assertEqual([(s.station_id, round(s.gallons, 6)) for s in e.stops], [(1, 30.0)])
        self.assertAlmostEqual(e.cost, 90.0)

    def test_gap_with_no_station_in_reach_has_empty_partial_plan(self):
        with self.assertRaises(NoFeasibleRoute) as ctx:
            plan_stops([(1, 700, 3.0)], total_miles=900)
        self.assertEqual((ctx.exception.stops, ctx.exception.cost, ctx.exception.mile), ([], 0.0, 0.0))

    def test_gap_longer_than_range_raises(self):
        with self.assertRaises(NoFeasibleRoute):
            plan_stops([(1, 100, 3.0)], total_miles=900)
