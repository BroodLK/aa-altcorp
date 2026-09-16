from datetime import timedelta
from unittest.mock import patch
from urllib.error import HTTPError

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from aa_altcorp.models import AltCorporation, AltCorpReview, AltCorpSettings
from aa_altcorp.services import (
    audit_relationship,
    notify_review,
    post_webhook,
    search_users,
)


class ServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("main", email="main@example.test")
        self.relationship = AltCorporation.objects.create(
            user=self.user, corporation_id=123, corporation_name="Example Corp"
        )
        self.settings = AltCorpSettings.current()
        self.settings.approved_states = ["member"]
        self.settings.standing_target_id = 456
        self.settings.save()

    def test_user_search_matches_name_and_email(self):
        self.assertEqual(list(search_users("example")), [self.user])

    @patch("aa_altcorp.services.aa_contact_for")
    def test_audit_requires_approved_state_and_positive_contact(self, contact_for):
        contact_for.return_value = type("Contact", (), {"standing": 5})()

        review = audit_relationship(self.relationship)

        # A positive contact alone is not enough; the state has to match too.
        self.assertFalse(review.approved)
        # The recorded state name legitimately differs between the two test
        # projects: without Alliance Auth there is no profile to read, and with
        # it a fresh user sits in Guest. Neither is an approved state, which is
        # the thing the audit actually turns on.
        self.assertNotIn(review.member_state, self.settings.approved_states)

    @patch("aa_altcorp.services.urlopen")
    def test_notification_records_first_delivery(self, urlopen):
        review = AltCorpReview.objects.create(
            relationship=self.relationship, approved=False, reason="bad standing"
        )
        self.settings.webhook_url = "https://example.test/webhook"
        self.settings.notification_interval = timedelta(days=1)
        self.settings.save()
        response = type(
            "Response",
            (),
            {
                "__enter__": lambda self: self,
                "__exit__": lambda *args: None,
                "read": lambda self: b"",
            },
        )()
        urlopen.return_value = response
        self.assertTrue(notify_review(review))
        review.refresh_from_db()
        self.assertIsNotNone(review.first_notified_at)
        self.assertEqual(review.first_notified_at, review.last_notified_at)
        self.assertFalse(notify_review(review))
        self.assertEqual(urlopen.call_count, 1)

    def test_notification_due_after_interval(self):
        review = AltCorpReview.objects.create(
            relationship=self.relationship,
            approved=False,
            last_notified_at=timezone.now() - timedelta(days=2),
        )
        self.assertTrue(review.notification_due(timedelta(days=1)))

    @patch("aa_altcorp.services.urlopen")
    def test_notification_survives_webhook_failure(self, urlopen):
        """A webhook 5xx must not propagate; it used to abort the whole audit loop."""
        review = AltCorpReview.objects.create(
            relationship=self.relationship, approved=False, reason="bad standing"
        )
        self.settings.webhook_url = "https://example.test/webhook"
        self.settings.notification_interval = timedelta(days=1)
        self.settings.save()
        urlopen.side_effect = HTTPError(
            "https://example.test/webhook", 500, "Server Error", {}, None
        )
        self.assertFalse(notify_review(review))
        review.refresh_from_db()
        self.assertIsNone(review.first_notified_at)
        self.assertIsNone(review.last_notified_at)

    @patch("aa_altcorp.services.urlopen")
    def test_post_webhook_reports_transport_failure(self, urlopen):
        urlopen.side_effect = OSError("connection refused")
        self.assertFalse(post_webhook("https://example.test/webhook", {"content": "hi"}))

    def test_post_webhook_without_url_is_a_no_op(self):
        self.assertFalse(post_webhook("", {"content": "hi"}))
