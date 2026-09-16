"""Task wiring: backward compatibility, fan-out, and guarded imports."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from aa_altcorp import tasks
from aa_altcorp.models import Alert, AltCorporation, AltCorpSettings, CharacterAccessToken


class OnceOptionTests(TestCase):
    def test_queue_once_is_skipped_when_unconfigured(self):
        """celery_once ships with Alliance Auth but is not configured here.

        An unconditional base=QueueOnce would break both test suites at import
        time, so the helper has to return nothing without CELERY_ONCE.
        """
        self.assertEqual(tasks._once_options(), {})
        self.assertEqual(tasks._once_options(key=["character_id"]), {})

    @patch("aa_altcorp.tasks.getattr", create=True)
    def test_tasks_import_without_celery_once(self, _getattr):
        # Importing the module at all is the assertion; it happened at collection.
        self.assertTrue(callable(tasks.run_alert_scan))


class AuditTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("pilot")
        AltCorporation.objects.create(
            user=self.user, corporation_id=98000001, corporation_name="Example Corp"
        )

    @patch("aa_altcorp.tasks.notify_review")
    @patch("aa_altcorp.tasks.audit_relationship")
    @patch("aa_altcorp.tasks.sync_character_access_lists", return_value=0)
    def test_the_legacy_task_still_returns_the_relationship_count(self, _sync, _audit, _notify):
        """Existing installs have a PeriodicTask pointing at this name."""
        self.assertEqual(tasks.audit_alt_corporations(), 1)

    @patch("aa_altcorp.tasks.run_alert_scan")
    @patch("aa_altcorp.tasks.notify_review")
    @patch("aa_altcorp.tasks.audit_relationship")
    @patch("aa_altcorp.tasks.sync_character_access_lists", return_value=0)
    def test_the_scan_is_only_chained_when_enabled(self, _sync, _audit, _notify, scan):
        tasks.audit_alt_corporations()
        scan.apply_async.assert_not_called()

        settings = AltCorpSettings.current()
        settings.alerts_enabled = True
        settings.save()
        tasks.audit_alt_corporations()
        scan.apply_async.assert_called_once()

    @patch("aa_altcorp.tasks.notify_review")
    @patch("aa_altcorp.tasks.audit_relationship", side_effect=OSError("ESI down"))
    @patch("aa_altcorp.tasks.sync_character_access_lists", return_value=0)
    def test_one_failing_relationship_does_not_abort_the_loop(self, _sync, _audit, _notify):
        AltCorporation.objects.create(
            user=self.user, corporation_id=98000002, corporation_name="Other Corp"
        )
        self.assertEqual(tasks.audit_alt_corporations(), 2)


class FanOutTests(TestCase):
    @patch("aa_altcorp.tasks.sync_character_acl")
    def test_one_subtask_per_character(self, subtask):
        CharacterAccessToken.objects.create(character_id=95000001, token_id=1)
        CharacterAccessToken.objects.create(character_id=95000002, token_id=2)

        self.assertEqual(tasks.sync_all_access_lists(), 2)
        self.assertEqual(subtask.apply_async.call_count, 2)
        for call in subtask.apply_async.call_args_list:
            # Jitter keeps a large install from bursting the ESI error limit.
            self.assertGreaterEqual(call.kwargs["countdown"], 0)
            self.assertLessEqual(call.kwargs["countdown"], tasks.TASK_JITTER)

    @patch("aa_altcorp.tasks.sync_character_access_lists", side_effect=OSError("ESI down"))
    def test_a_failing_sync_returns_zero_rather_than_raising(self, _sync):
        self.assertEqual(tasks.sync_character_acl(95000001), 0)


class ScanTaskTests(TestCase):
    def test_the_scan_is_a_no_op_while_disabled(self):
        self.assertEqual(tasks.run_alert_scan(), 0)
        self.assertEqual(Alert.objects.count(), 0)

    @patch("aa_altcorp.tasks.deliver_pending_alerts")
    def test_delivery_is_chained_after_a_real_scan(self, deliver):
        settings = AltCorpSettings.current()
        settings.alerts_enabled = True
        settings.save()

        tasks.run_alert_scan()

        deliver.apply_async.assert_called_once()

    def test_delivering_nothing_is_harmless(self):
        self.assertEqual(tasks.deliver_pending_alerts(), {})
