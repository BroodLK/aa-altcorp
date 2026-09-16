"""Root URLs for tests."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("aa_altcorp.urls")),
]
