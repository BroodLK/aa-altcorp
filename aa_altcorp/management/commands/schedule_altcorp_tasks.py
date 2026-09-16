"""Install the default Alt Corp periodic tasks in Celery Beat."""

from django.core.management.base import BaseCommand, CommandError

AUDIT_TASK_NAME = "aa-altcorp-audit-and-acl-sync"
SCAN_TASK_NAME = "aa-altcorp-alert-scan"
REFRESH_TASK_NAME = "aa-altcorp-refresh-alert-messages"

#: How often to re-render Discord messages whose alert narrowed or resolved with
#: no interaction to edit through -- a scan changing it, or an exemption revoked
#: in Django admin. Without this the buttons on a posted message go stale.
REFRESH_INTERVAL_MINUTES = 15


class Command(BaseCommand):
    help = (
        "Create or update the Alt Corp audit, ACL sync, alert scan, "
        "and Discord message refresh tasks."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=1,
            help="Run the audit and ACL sync every this many hours (default: 1).",
        )
        parser.add_argument(
            "--scan-cron",
            default=None,
            help=(
                "Five-field cron for the alert scan. "
                "Defaults to the notification interval in Alt Corp settings."
            ),
        )
        parser.add_argument(
            "--remove",
            action="store_true",
            help="Delete every scheduled task instead of creating them.",
        )

    def handle(self, *args, **options):
        models = self._beat_models()
        if options["remove"]:
            return self._remove(models)

        hours = options["hours"]
        if hours < 1:
            raise CommandError("--hours must be at least 1.")

        self._schedule_audit(models, hours)
        self._schedule_scan(models, options["scan_cron"])
        self._schedule_refresh(models)

    # -- helpers ------------------------------------------------------------

    def _beat_models(self):
        try:
            from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask
        except ImportError as exc:
            raise CommandError(
                "django-celery-beat must be installed and enabled before scheduling tasks."
            ) from exc
        return CrontabSchedule, IntervalSchedule, PeriodicTask

    def _schedule_audit(self, models, hours):
        _, IntervalSchedule, PeriodicTask = models
        schedule, _ = IntervalSchedule.objects.update_or_create(
            every=hours,
            period=IntervalSchedule.HOURS,
            defaults={},
        )
        task, created = PeriodicTask.objects.update_or_create(
            name=AUDIT_TASK_NAME,
            defaults={
                "task": "aa_altcorp.tasks.audit_alt_corporations",
                "interval": schedule,
                "crontab": None,
                "enabled": True,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(f"{action} {task.name}; it will run every {hours} hour(s).")
        )

    def _schedule_scan(self, models, scan_cron):
        CrontabSchedule, _, PeriodicTask = models
        from ...models import AltCorpSettings

        cron = scan_cron or AltCorpSettings.current().notification_interval
        fields = cron.split()
        if len(fields) != 5:
            raise CommandError(f"--scan-cron needs exactly five fields, got {cron!r}.")
        minute, hour, day_of_month, month_of_year, day_of_week = fields

        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            day_of_week=day_of_week,
        )
        task, created = PeriodicTask.objects.update_or_create(
            name=SCAN_TASK_NAME,
            defaults={
                "task": "aa_altcorp.tasks.run_alert_scan",
                "crontab": schedule,
                "interval": None,
                "enabled": True,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} {task.name} on cron '{cron}'."))

    def _schedule_refresh(self, models):
        _, IntervalSchedule, PeriodicTask = models
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=REFRESH_INTERVAL_MINUTES,
            period=IntervalSchedule.MINUTES,
        )
        task, created = PeriodicTask.objects.update_or_create(
            name=REFRESH_TASK_NAME,
            defaults={
                "task": "aa_altcorp.tasks.refresh_alert_messages",
                "interval": schedule,
                "crontab": None,
                "enabled": True,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {task.name}; it will run every {REFRESH_INTERVAL_MINUTES} minute(s)."
            )
        )

    def _remove(self, models):
        _, _, PeriodicTask = models
        deleted, _ = PeriodicTask.objects.filter(
            name__in=(AUDIT_TASK_NAME, SCAN_TASK_NAME, REFRESH_TASK_NAME)
        ).delete()
        self.stdout.write(self.style.SUCCESS(f"Removed {deleted} scheduled task(s)."))
