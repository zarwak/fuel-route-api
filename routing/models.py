from django.db import models


class Station(models.Model):
    """One fuel station, cleaned and located (city-level) by `load_stations`."""

    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    price = models.DecimalField(max_digits=6, decimal_places=3)  # $ per gallon
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta:
        indexes = [models.Index(fields=["lat", "lon"])]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"


class Place(models.Model):
    """A US city/town/place from the Census Gazetteer, so 'Chicago, IL' resolves offline (no API call)."""

    state = models.CharField(max_length=2)
    key = models.CharField(max_length=100)  # normalised name, see load_stations.norm
    name = models.CharField(max_length=120)
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["state", "key"], name="unique_place_per_state")]

    def __str__(self):
        return f"{self.name}, {self.state}"
