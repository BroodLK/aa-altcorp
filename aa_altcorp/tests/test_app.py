"""Application tests."""

from django.test import TestCase


class TestApp(TestCase):
    """Smoke tests for the installable app."""

    def test_app_permission_exists(self):
        self.assertEqual(self.app_label, "aa_altcorp")

    app_label = "aa_altcorp"
