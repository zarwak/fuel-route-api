from django.urls import path

from . import views

urlpatterns = [
    path("route", views.route_view, name="route"),
]
