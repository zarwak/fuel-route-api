"""Offline 'City, ST' lookup: parsing, database lookup, and how the API uses it."""
import json
from unittest import mock

from django.test import SimpleTestCase, TestCase

from . import ors, places
from .management.commands.load_stations import norm
from .models import Place
from .test_api import LAT, straight_route


class ParseTests(SimpleTestCase):
    def test_accepts_abbreviation_full_name_and_country_suffix(self):
        for text, want in [
            ("Chicago, IL", ("Chicago", "IL")),
            ("  chicago ,  il ", ("chicago", "IL")),
            ("Chicago, Illinois", ("Chicago", "IL")),
            ("Chicago, IL, USA", ("Chicago", "IL")),
            ("Salt Lake City, Utah, United States", ("Salt Lake City", "UT")),
            ("Washington, District of Columbia", ("Washington", "DC")),
        ]:
            self.assertEqual(places.parse_city_state(text), want, text)

    def test_rejects_anything_that_is_not_city_comma_state(self):
        for text in ["Chicago", "Chicago, Narnia", ", IL", "Paris, France", "60601", "IL"]:
            self.assertIsNone(places.parse_city_state(text), text)


class LookupTests(TestCase):
    def setUp(self):
        Place.objects.create(state="IL", key=norm("Chicago"), name="Chicago", lat=41.83, lon=-87.68)

    def test_finds_place_and_labels_it(self):
        p = places.lookup("chicago, illinois")
        self.assertEqual((p["lat"], p["lon"], p["label"], p["source"]), (41.83, -87.68, "Chicago, IL, USA", "census-offline"))

    def test_unambiguous_prefix_matches_but_ambiguous_does_not(self):
        Place.objects.create(state="ID", key=norm("Boise City"), name="Boise City", lat=43.6, lon=-116.2)
        Place.objects.create(state="TX", key=norm("Salem Heights"), name="Salem Heights", lat=30.0, lon=-97.0)
        Place.objects.create(state="TX", key=norm("Salem Park"), name="Salem Park", lat=31.0, lon=-98.0)
        self.assertEqual(places.lookup("Boise, ID")["label"], "Boise City, ID, USA")
        self.assertIsNone(places.lookup("Salem, TX"))  # two candidates: let the geocoder decide

    def test_unknown_city_or_street_address_returns_none(self):
        self.assertIsNone(places.lookup("Gotham, IL"))
        self.assertIsNone(places.lookup("123 Main St, Chicago, IL"))  # needs the real geocoder


class ApiOfflineTests(TestCase):
    url = "/api/route"

    def setUp(self):
        Place.objects.create(state="KS", key=norm("Alpha"), name="Alpha", lat=LAT, lon=-100.0)
        Place.objects.create(state="KS", key=norm("Beta"), name="Beta", lat=LAT, lon=-80.0)
        self.route = straight_route(-100.0, -90.0)  # ~530 miles; no stations needed to test resolution
        p = mock.patch("routing.views.ors.drive_route", return_value=self.route)
        g = mock.patch("routing.views.ors.geocode",
                       side_effect=lambda text, calls: {"lat": LAT, "lon": -95.0, "label": text, "source": "openrouteservice"})
        self.drive, self.geocode = p.start(), g.start()
        self.addCleanup(p.stop)
        self.addCleanup(g.stop)

    def post(self, payload):
        return self.client.post(self.url, json.dumps(payload), content_type="application/json")

    def test_city_state_resolves_offline_without_geocoding(self):
        r = self.post({"start": "Alpha, KS", "finish": "Beta, Kansas"})
        self.assertEqual(r.status_code, 200)
        self.geocode.assert_not_called()
        d = r.json()
        self.assertEqual(d["start"]["source"], "census-offline")
        self.assertEqual(d["start"]["label"], "Alpha, KS, USA")
        self.assertNotIn("query", d["start"])  # internal detail stays internal

    def test_unknown_city_falls_back_to_geocoder(self):
        self.post({"start": "Alpha, KS", "finish": "Gamma, KS"})
        self.assertEqual([c.args[0] for c in self.geocode.call_args_list], ["Gamma, KS"])

    def test_offline_point_with_no_road_is_retried_via_geocoder(self):
        self.drive.side_effect = [ors.NoRoute("no road near point"), self.route]
        r = self.post({"start": "Alpha, KS", "finish": "Beta, KS"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.drive.call_count, 2)
        self.assertEqual(self.geocode.call_count, 2)
        self.assertEqual(r.json()["start"]["source"], "openrouteservice")

    def test_still_no_route_after_retry_is_422(self):
        self.drive.side_effect = ors.NoRoute("no route")
        r = self.post({"start": "Alpha, KS", "finish": "Beta, KS"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.drive.call_count, 2)  # one retry, not a loop

    def test_no_retry_when_nothing_was_resolved_offline(self):
        self.drive.side_effect = ors.NoRoute("no route")
        r = self.post({"start": "1,2 x", "finish": "Some Street 5"})  # both go to the geocoder
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.drive.call_count, 1)
