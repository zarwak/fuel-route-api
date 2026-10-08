import csv
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from routing.models import Place, Station

CANADA = {"AB", "BC", "MB", "NB", "NS", "ON", "QC", "SK", "YT"}
SUFFIX = re.compile(
    r"\s+(city and borough|metropolitan government|consolidated government|"
    r"unified government|urban county|zona urbana|municipality|comunidad|"
    r"township|borough|village|city|town|CDP|CCD|plantation|reservation|district)(\s*\(.*\))?$",
    re.I,
)


def norm(name):
    """Make 'St. Louis', 'Saint Louis' and 'Mc Lean'/'McLean' compare equal."""
    s = name.strip().lower()
    s = re.sub(r"\bsaint\b", "st", s)
    s = re.sub(r"\b(st|ft|mt)\.?(?=\s|$)", lambda m: {"st": "st", "ft": "fort", "mt": "mount"}[m.group(1)], s)
    return re.sub(r"[^a-z0-9]", "", s)


def find_place(places, state, city):
    """Exact match first; else accept a prefix match only if it is unambiguous
    (e.g. 'Boise' -> 'Boise City', 'Macon' -> 'Macon-Bibb County')."""
    key = norm(city)
    if (state, key) in places:
        return places[(state, key)]
    hits = [v for (st, name), v in places.items() if st == state and name.startswith(key)]
    return hits[0] if len(hits) == 1 else None


def read_gazetteer(path):
    """Yield (state, clean_name, key, lat, lon, land_area_sq_mi) for each row of a Census Gazetteer file."""
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            row = {k.strip(): v.strip() for k, v in row.items()}
            name = SUFFIX.sub("", row["NAME"])
            yield (row["USPS"], name, norm(name), float(row["INTPTLAT"]), float(row["INTPTLONG"]),
                   float(row["ALAND_SQMI"]))


def load_gazetteer(path):
    return {(st, key): (lat, lon) for st, _, key, lat, lon, _ in read_gazetteer(path)}


def build_places(files):
    """One entry per (state, name). Within a file the larger place wins; later files override earlier ones
    (county subdivisions sort before 'place' files, so real cities beat township names)."""
    merged = {}
    for gaz in files:
        best = {}
        for st, name, key, lat, lon, area in read_gazetteer(gaz):
            if (st, key) not in best or area > best[(st, key)][0]:
                best[(st, key)] = (area, name, lat, lon)
        merged.update(best)
    return merged


class Command(BaseCommand):
    help = "Clean the OPIS price CSV and load US stations (cheapest price per station) into the DB."

    def handle(self, *args, **opts):
        data = Path(settings.BASE_DIR) / "data"
        files = sorted(data.glob("*_Gaz_*_national.txt"))
        places = {}
        for gaz in files:
            places.update(load_gazetteer(gaz))

        cheapest = {}  # opis_id -> row; keep the lowest price per station
        with open(data / "fuel-prices-for-be-assessment.csv", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row["State"] in CANADA:
                    continue
                key = int(row["OPIS Truckstop ID"])
                price = Decimal(row["Retail Price"]).quantize(Decimal("0.001"))
                if key not in cheapest or price < cheapest[key][1]:
                    cheapest[key] = (row, price)

        stations, unplaced = [], 0
        for key, (row, price) in cheapest.items():
            pos = find_place(places, row["State"], row["City"])
            if pos is None:
                unplaced += 1
                continue
            stations.append(
                Station(
                    opis_id=key,
                    name=row["Truckstop Name"].strip(),
                    address=row["Address"].strip(),
                    city=row["City"].strip(),
                    state=row["State"],
                    price=price,
                    lat=pos[0],
                    lon=pos[1],
                )
            )

        Station.objects.all().delete()
        Station.objects.bulk_create(stations)
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(stations)} stations; {unplaced} skipped (city not found)."
        ))

        Place.objects.all().delete()
        Place.objects.bulk_create(
            (Place(state=st, key=key, name=name, lat=lat, lon=lon) for (st, key), (_, name, lat, lon)
             in build_places(files).items()),
            batch_size=5000,
        )
        self.stdout.write(self.style.SUCCESS(f"Loaded {Place.objects.count()} places for offline city lookup."))
