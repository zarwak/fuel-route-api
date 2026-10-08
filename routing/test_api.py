"""API, geometry and client tests. The routing service is always faked: no key or network needed."""
import json
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from . import ors
from .geo import haversine, mile_markers, stations_along_route
from .models import Station

LAT = 40.0  # a straight west->east test route along this latitude


def straight_route(lon_from=-100.0, lon_to=-80.0, step=0.1):
    n = int(round((lon_to - lon_from) / step))
    coords = [[lon_from + i * step, LAT] for i in range(n + 1)]
    miles = sum(haversine(LAT, a[0], LAT, b[0]) for a, b in zip(coords, coords[1:]))
    return {"coords": coords, "miles": miles, "hours": miles / 60}


def station(opis_id, lon, price, lat=LAT):
    return Station.objects.create(
        opis_id=opis_id, name=f"Stop {opis_id}", address="I-70", city="Town", state="KS",
        price=price, lat=lat, lon=lon,
    )


class GeoTests(SimpleTestCase):
    def test_haversine_chicago_to_dallas(self):
        self.assertAlmostEqual(haversine(41.88, -87.63, 32.78, -96.80), 802, delta=5)

    def test_mile_markers_start_at_zero_and_end_at_total(self):
        route = straight_route()
        m = mile_markers(route["coords"], 1000.0)
        self.assertEqual(m[0], 0.0)
        self.assertAlmostEqual(m[-1], 1000.0)
        self.assertEqual(m, sorted(m))

    def test_only_stations_within_tolerance_are_found(self):
        route = straight_route()
        markers = mile_markers(route["coords"], route["miles"])
        near = Station(opis_id=1, lat=LAT + 0.03, lon=-90.0)  # ~2 miles off
        far = Station(opis_id=2, lat=LAT + 0.5, lon=-90.0)  # ~35 miles off
        found = stations_along_route([near, far], route["coords"], markers, 5)
        self.assertEqual([s.opis_id for s, _, _ in found], [1])
        self.assertAlmostEqual(found[0][1], route["miles"] / 2, delta=5)


class RouteApiTests(TestCase):
    url = "/api/route"

    def setUp(self):
        self.route = straight_route()  # ~1,010 miles
        p = mock.patch("routing.views.ors.drive_route", return_value=self.route)
        g = mock.patch(
            "routing.views.ors.geocode",
            side_effect=lambda text, calls: {"lat": LAT, "lon": -100.0 if "A" in text else -80.0, "label": text},
        )
        self.drive, self.geocode = p.start(), g.start()
        self.addCleanup(p.stop)
        self.addCleanup(g.stop)

    def post(self, payload):
        return self.client.post(self.url, json.dumps(payload), content_type="application/json")

    def test_returns_route_stops_and_consistent_totals(self):
        station(1, -92.0, "3.000")
        station(2, -86.0, "3.500")
        r = self.post({"start": "City A", "finish": "City B"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertAlmostEqual(d["gallons_used"], d["distance_miles"] / 10, delta=0.01)
        self.assertGreater(len(d["fuel_stops"]), 0)
        self.assertAlmostEqual(d["total_fuel_cost"], sum(s["cost"] for s in d["fuel_stops"]), delta=0.02)
        kinds = [f["properties"]["kind"] for f in d["map"]["features"]]
        self.assertEqual(kinds[:3], ["route", "start", "finish"])
        self.assertEqual(kinds.count("fuel_stop"), len(d["fuel_stops"]))
        self.assertEqual(d["map"]["features"][0]["geometry"]["type"], "LineString")

    def test_never_exceeds_vehicle_range_between_stops(self):
        station(1, -92.0, "3.000")
        station(2, -86.0, "3.500")
        d = self.post({"start": "City A", "finish": "City B"}).json()
        marks = [0.0] + [s["mile_marker"] for s in d["fuel_stops"]] + [d["distance_miles"]]
        self.assertTrue(all(b - a <= 500.5 for a, b in zip(marks, marks[1:])), marks)

    def test_prefers_cheaper_of_two_nearby_stations(self):
        station(1, -92.0, "4.000")
        station(2, -91.9, "3.000")
        station(3, -86.0, "3.900")
        d = self.post({"start": "City A", "finish": "City B"}).json()
        self.assertIn("Stop 2", [s["name"] for s in d["fuel_stops"]])
        self.assertNotIn("Stop 1", [s["name"] for s in d["fuel_stops"]])

    def test_coordinates_skip_geocoding(self):
        station(1, -92.0, "3.000")
        station(2, -86.0, "3.500")
        r = self.post({"start": {"lat": LAT, "lon": -100.0}, "finish": f"{LAT},-80.0"})
        self.assertEqual(r.status_code, 200)
        self.geocode.assert_not_called()

    def test_get_with_query_string(self):
        station(1, -92.0, "3.000")
        station(2, -86.0, "3.500")
        r = self.client.get(self.url, {"start": "City A", "finish": "City B"})
        self.assertEqual(r.status_code, 200)

    def test_short_trip_has_no_stops_and_no_cost(self):
        self.drive.return_value = straight_route(-90.0, -88.0)  # ~100 miles
        d = self.post({"start": "City A", "finish": "City B"}).json()
        self.assertEqual((d["fuel_stops"], d["total_fuel_cost"]), ([], 0.0))

    def test_widens_search_when_nothing_is_close_enough(self):
        # Only stations ~20 miles off the road, but the trip needs a stop.
        station(1, -92.0, "3.000", lat=LAT + 0.29)
        station(2, -86.0, "3.000", lat=LAT + 0.29)
        r = self.post({"start": "City A", "finish": "City B"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["meta"]["stations_searched_within_miles"], 30)

    def test_no_stations_on_long_trip_returns_route_with_warning(self):
        r = self.post({"start": "City A", "finish": "City B"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertFalse(d["plan_complete"])
        self.assertIn("No fuel station", d["warning"])
        self.assertEqual((d["fuel_stops"], d["total_fuel_cost"]), ([], 0.0))
        self.assertEqual(d["map"]["features"][0]["geometry"]["type"], "LineString")  # route still returned

    def test_partial_plan_covers_the_route_up_to_the_gap(self):
        station(1, -92.0, "3.000")  # ~mile 424; the remaining ~640 miles have no station at all
        d = self.post({"start": "City A", "finish": "City B"}).json()
        self.assertFalse(d["plan_complete"])
        self.assertEqual([s["name"] for s in d["fuel_stops"]], ["Stop 1"])
        self.assertGreater(d["total_fuel_cost"], 0)
        self.assertRegex(d["warning"], r"after mile 42\d")

    def test_complete_plan_has_no_warning(self):
        station(1, -92.0, "3.000")
        station(2, -86.0, "3.500")
        d = self.post({"start": "City A", "finish": "City B"}).json()
        self.assertTrue(d["plan_complete"])
        self.assertIsNone(d["warning"])

    def test_validation_errors(self):
        cases = [
            ({"start": "City A"}, "finish"),
            ({"finish": "City B"}, "start"),
            ({"start": "City A", "finish": {"lat": 51.5, "lon": -0.12}}, "outside the USA"),
            ({"start": {"lat": "x", "lon": 1}, "finish": "City B"}, "lat"),
        ]
        for payload, expected in cases:
            r = self.post(payload)
            self.assertEqual(r.status_code, 400, payload)
            self.assertIn(expected, r.json()["error"])

    def test_bad_json_and_non_object_body_are_400(self):
        r = self.client.post(self.url, "{oops", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.post(["a", "b"]).status_code, 400)

    def test_unknown_place_is_404(self):
        self.geocode.side_effect = ors.PlaceNotFound("No US place found for 'zzz'")
        self.assertEqual(self.post({"start": "zzz", "finish": "City B"}).status_code, 404)

    def test_routing_service_failure_is_502(self):
        self.drive.side_effect = ors.ORSError("boom")
        self.assertEqual(self.post({"start": "City A", "finish": "City B"}).status_code, 502)

    def test_unroutable_places_are_422_not_a_server_error(self):
        self.drive.side_effect = ors.NoRoute("No driving route found")
        r = self.post({"start": "City A", "finish": "City B"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("route", r.json()["error"])

    def test_overlong_place_name_is_400_and_never_geocoded(self):
        r = self.post({"start": "x" * 5000, "finish": "City B"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("too long", r.json()["error"])
        self.geocode.assert_not_called()

    def test_wrong_method_is_405(self):
        self.assertEqual(self.client.put(self.url).status_code, 405)


class OrsClientTests(SimpleTestCase):
    """The real client code, with `requests` faked."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @staticmethod
    def response(payload, status=200):
        r = mock.Mock(status_code=status, text="")
        r.json.return_value = payload
        return r

    def test_geocode_is_cached_after_first_call(self):
        payload = {"features": [{"geometry": {"coordinates": [-87.6, 41.9]}, "properties": {"label": "Chicago"}}]}
        calls = []
        with mock.patch("routing.ors.requests.get", return_value=self.response(payload)) as get, \
                mock.patch("routing.ors.settings.ORS_API_KEY", "k"):
            first = ors.geocode("Chicago, IL", calls)
            second = ors.geocode("  chicago,  il ", calls)
        self.assertEqual(first, second)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(calls, ["geocode"])
        self.assertEqual((first["lat"], first["lon"]), (41.9, -87.6))

    def test_geocode_without_results_raises(self):
        with mock.patch("routing.ors.requests.get", return_value=self.response({"features": []})), \
                mock.patch("routing.ors.settings.ORS_API_KEY", "k"):
            with self.assertRaises(ors.PlaceNotFound):
                ors.geocode("nowhere", [])

    def test_missing_key_raises_clear_error(self):
        with mock.patch("routing.ors.settings.ORS_API_KEY", ""):
            with self.assertRaises(ors.ORSError):
                ors.geocode("Chicago", [])

    def test_route_converts_meters_to_miles_and_is_cached(self):
        payload = {"features": [{"geometry": {"coordinates": [[0, 0], [1, 1]]},
                                 "properties": {"summary": {"distance": 1609.344 * 100, "duration": 7200}}}]}
        calls = []
        a, b = {"lat": 1.0, "lon": 2.0}, {"lat": 3.0, "lon": 4.0}
        with mock.patch("routing.ors.requests.post", return_value=self.response(payload)) as post, \
                mock.patch("routing.ors.settings.ORS_API_KEY", "k"):
            r1 = ors.drive_route(a, b, calls)
            ors.drive_route(a, b, calls)
        self.assertAlmostEqual(r1["miles"], 100.0)
        self.assertAlmostEqual(r1["hours"], 2.0)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(calls, ["route"])

    def test_route_404_with_no_route_code_raises_noroute(self):
        for code in (2009, 2010):
            bad = self.response({"error": {"code": code, "message": "x"}}, status=404)
            with mock.patch("routing.ors.requests.post", return_value=bad),                     mock.patch("routing.ors.settings.ORS_API_KEY", "k"):
                with self.assertRaises(ors.NoRoute):
                    ors.drive_route({"lat": 1.0, "lon": 2.0}, {"lat": 3.0, "lon": 4.0}, [])

    def test_route_http_error_raises(self):
        with mock.patch("routing.ors.requests.post", return_value=self.response({}, status=500)), \
                mock.patch("routing.ors.settings.ORS_API_KEY", "k"):
            with self.assertRaises(ors.ORSError):
                ors.drive_route({"lat": 1.0, "lon": 2.0}, {"lat": 3.0, "lon": 4.0}, [])


class MapPageTests(SimpleTestCase):
    def test_home_serves_the_leaflet_page(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "leaflet")
        self.assertContains(r, "/api/route")
