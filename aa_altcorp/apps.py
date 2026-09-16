"""App configuration."""

from django.apps import AppConfig

from aa_altcorp import __version__


class AaAltcorpConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aa_altcorp"
    label = "aa_altcorp"
    verbose_name = f"AA Alt Corp v{__version__}"
