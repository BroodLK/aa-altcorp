"""The schedule_altcorp_tasks management command."""

from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from aa_altcorp.management.commands.schedule_altcorp_tasks import (
    AUDIT_TASK_NAME,
    REFRESH_TASK_NAME,
    SCAN_TASK_NAME,
)

ALL_TASK_NAMES = (AUDIT_TASK_NAME, SCAN_TASK_NAME, REFRESH_TASK_NAME)


def _schedule(*args):
    out = StringIO()
    call_command("schedule_altcorp_tasks", *args, stdout=out)
    return out.getvalue()


class ScheduleCommandTests(TestCase):
    def setUp(self):
        try:
            from django_celery_beat.models import PeriodicTask
        except ImportError:  # pragma: no cover - dependency is declared
            self.skipTest("django-celery-beat is not installed")
        self.PeriodicTask = PeriodicTask

    def test_it_creates_every_task(self):
        output = _schedule()

        for name in ALL_TASK_NAMES:
            self.assertIn(name, output)
        audit = self.PeriodicTask.objects.get(name=AUDIT_TASK_NAME)
        scan = self.PeriodicTask.objects.get(name=SCAN_TASK_NAME)
        self.assertEqual(audit.task, "aa_altcorp.tasks.audit_alt_corporations")
        self.assertEqual(scan.task, "aa_altcorp.tasks.run_alert_scan")
        # An interval task and a crontab task must not carry both schedules.
        self.assertIsNone(audit.crontab)
        self.assertIsNone(scan.interval)

    def test_the_scan_cron_defaults_to_the_notification_interval(self):
        _schedule()

        scan = self.PeriodicTask.objects.get(name=SCAN_TASK_NAME)
        self.assertEqual(scan.crontab.minute, "0")
        self.assertEqual(scan.crontab.hour, "0")

    def test_an_explicit_cron_is_used(self):
        _schedule("--scan-cron", "17 * * * *")

        scan = self.PeriodicTask.objects.get(name=SCAN_TASK_NAME)
        self.assertEqual(scan.crontab.minute, "17")

    def test_it_schedules_the_discord_message_refresh(self):
        """Without this, a narrowed or resolved alert keeps its stale buttons."""
        _schedule()

        refresh = self.PeriodicTask.objects.get(name=REFRESH_TASK_NAME)
        self.assertEqual(refresh.task, "aa_altcorp.tasks.refresh_alert_messages")
        self.assertIsNone(refresh.crontab)
        self.assertIsNotNone(refresh.interval)

    def test_running_it_twice_is_idempotent(self):
        _schedule()
        _schedule()

        for name in ALL_TASK_NAMES:
            self.assertEqual(self.PeriodicTask.objects.filter(name=name).count(), 1)

    def test_remove_deletes_every_task(self):
        _schedule()

        _schedule("--remove")

        self.assertFalse(self.PeriodicTask.objects.filter(name__in=ALL_TASK_NAMES).exists())

    def test_a_malformed_cron_is_rejected(self):
        with self.assertRaisesMessage(CommandError, "exactly five fields"):
            _schedule("--scan-cron", "0 *")

    def test_hours_must_be_positive(self):
        with self.assertRaisesMessage(CommandError, "at least 1"):
            _schedule("--hours", "0")
