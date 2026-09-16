"""Get an alert to Discord: bot when available, webhook otherwise.

No Discord import.  The bot path only enqueues a Celery task; the coroutine it
names lives in :mod:`aa_altcorp.discord.bot_functions` and is imported by the
bot process, never here.
"""

import logging

from django.utils import timezone

from .. import services
from ..alerts import taxonomy
from ..models import AltCorpSettings
from . import embeds

logger = logging.getLogger(__name__)

#: The coroutine aadiscordbot runs with bot context on our behalf.
POST_ALERT_FUNCTION = "aa_altcorp.discord.bot_functions.post_alert"
EDIT_ALERT_FUNCTION = "aa_altcorp.discord.bot_functions.edit_alert_message"

SKIPPED = "skipped"


def deliver_alert(alert, settings=None):
    """Deliver one alert. Returns ``"bot"``, ``"webhook"`` or ``"skipped"``."""
    settings = settings or AltCorpSettings.current()
    mode = settings.alert_delivery
    if mode == taxonomy.AlertDelivery.NONE or not settings.enabled:
        return SKIPPED

    wants_bot = mode in (taxonomy.AlertDelivery.BOT, taxonomy.AlertDelivery.AUTO)
    if wants_bot and settings.discord_channel_id and _deliver_via_bot(alert):
        return _stamp(alert, taxonomy.DeliveryChannel.BOT)

    if mode == taxonomy.AlertDelivery.BOT:
        logger.warning("Alert %s not delivered: bot delivery is required but unavailable", alert.pk)
        return SKIPPED

    if settings.webhook_url and _deliver_via_webhook(alert, settings):
        return _stamp(alert, taxonomy.DeliveryChannel.WEBHOOK)

    logger.warning("Alert %s has no usable delivery route", alert.pk)
    return SKIPPED


def deliver_alerts(alerts, settings=None):
    """Deliver a batch, counting outcomes. One failure never stops the rest."""
    settings = settings or AltCorpSettings.current()
    counts = {}
    for alert in alerts:
        try:
            outcome = deliver_alert(alert, settings)
        except Exception:  # Delivery must never break a scan.
            logger.exception("Unexpected failure delivering alert %s", alert.pk)
            outcome = SKIPPED
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def request_message_refresh(alert):
    """Ask the bot to re-render an already-posted message.

    Used when there is no interaction to edit through: a scan narrowed or
    resolved the alert, or an admin revoked an exemption in Django admin. The
    channel and message ids come off the alert, so no settings are needed.
    """
    if not alert.discord_message_id or not alert.discord_channel_id:
        return False
    return _enqueue(EDIT_ALERT_FUNCTION, alert.pk)


def _deliver_via_bot(alert):
    return _enqueue(POST_ALERT_FUNCTION, alert.pk)


def _enqueue(function_path, alert_pk):
    """Hand a coroutine to aadiscordbot's queue consumer.

    run_task_function rather than send_channel_message_by_discord_id: the
    latter discards the sent message, so the message id needed for later edits
    could never be recorded.
    """
    try:
        from aadiscordbot.tasks import run_task_function
    except (ImportError, RuntimeError):
        logger.info("allianceauth-discordbot is not installed; falling back to the webhook")
        return False
    try:
        run_task_function.apply_async(
            args=[function_path],
            kwargs={"task_args": [int(alert_pk)], "task_kwargs": {}},
        )
    except Exception:  # A dead broker must fall through, not raise.
        logger.warning("Could not queue Discord task %s", function_path, exc_info=True)
        return False
    return True


def _deliver_via_webhook(alert, settings):
    """Plain webhook. No buttons: webhooks cannot carry interactive components."""
    payload = {
        "content": embeds.alert_summary_line(alert),
        "embeds": [embeds.alert_embed(alert)],
    }
    return services.post_webhook(settings.webhook_url, payload)


def _stamp(alert, channel):
    alert.notified_at = timezone.now()
    alert.delivery = channel
    alert.save(update_fields=("notified_at", "delivery", "last_seen_at"))
    return channel


def pending_alerts(settings=None, limit=None):
    """Open alerts that still need delivering, honouring the renotify interval."""
    from datetime import timedelta

    from django.db.models import Q

    from ..models import Alert

    settings = settings or AltCorpSettings.current()
    pending = Q(notified_at__isnull=True)
    if settings.renotify_interval_days:
        cutoff = timezone.now() - timedelta(days=settings.renotify_interval_days)
        pending |= Q(notified_at__lt=cutoff)
    queryset = (
        Alert.objects.filter(state=taxonomy.AlertState.OPEN)
        .filter(pending)
        .order_by("first_seen_at")
    )
    return queryset[: limit or settings.alert_batch_size]
