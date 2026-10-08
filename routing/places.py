"""Resolve 'City, ST' (or 'City, State Name') to coordinates from the local Census data: no API call."""
import re

from .management.commands.load_stations import norm
from .models import Place

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
ABBR_BY_NAME = {name.lower(): abbr for abbr, name in STATE_NAMES.items()}
COUNTRY_SUFFIX = re.compile(r"\s*,\s*(usa|u\.s\.a\.|us|u\.s\.|united states( of america)?)\s*$", re.I)


def parse_city_state(text):
    """'Chicago, IL' / 'chicago, illinois, USA' -> ('Chicago', 'IL'); anything else -> None."""
    text = COUNTRY_SUFFIX.sub("", text.strip())
    if "," not in text:
        return None
    city, state = (part.strip() for part in text.rsplit(",", 1))
    abbr = state.upper() if state.upper() in STATE_NAMES else ABBR_BY_NAME.get(state.lower())
    return (city, abbr) if city and abbr else None


def lookup(text):
    """Coordinates for a 'City, ST' text from the local Place table, or None (caller falls back to geocoding)."""
    parsed = parse_city_state(text)
    if not parsed:
        return None
    city, abbr = parsed
    key = norm(city)
    place = Place.objects.filter(state=abbr, key=key).first()
    if place is None and key:
        # 'Boise' -> 'Boise City', 'Nashville' -> 'Nashville-Davidson': accept a prefix only if unambiguous.
        hits = list(Place.objects.filter(state=abbr, key__startswith=key)[:2])
        place = hits[0] if len(hits) == 1 else None
    if place is None:
        return None
    return {"lat": place.lat, "lon": place.lon, "label": f"{place.name}, {abbr}, USA",
            "source": "census-offline", "query": text}
