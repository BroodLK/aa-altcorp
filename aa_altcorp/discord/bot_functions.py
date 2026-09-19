"""Coroutines aadiscordbot runs for us via ``run_task_function``.

Bot-side only: imports py-cord.

``run_task_function`` is the only aadiscordbot entry point that executes our own
code with bot context (``await func(bot, *task_args, **task_kwargs)``).
``send_channel_message_by_discord_id`` cannot be used instead, because it
discards the sent message and so can never report the message id we need in
order to edit the post later.
"""

import logging

import discord

from ..models import Alert, AltCorpSettings
from ..alerts import taxonomy
from . import dbsafe, embeds
from .views import AlertView
from . import actions

logger = logging.getLogger(__name__)


async def post_alert(bot, alert_pk):
    """Post an alert, then record the message id for later edits."""
    payload = await dbsafe.run_db(_load_for_post, alert_pk)
    if payload is None:
        logger.info("Alert %s vanished before it could be posted", alert_pk)
        return
    channel_id, embed, actions_and_labels = payload
    view = _make_view(actions_and_labels)

    channel = bot.get_channel(channel_id)
    if channel is None:
        # Raise rather than swallow: aadiscordbot's task runner reports this,
        # and a silently undelivered alert is worse than a logged failure.
        raise RuntimeError(f"Discord channel {channel_id} is not visible to the bot")

    message = await channel.send(embed=discord.Embed.from_dict(embed), view=view)
    await dbsafe.run_db(_record_message, alert_pk, channel_id, message.id)


async def edit_alert_message(bot, alert_pk):
    """Re-render a posted message when there is no interaction to edit through."""
    payload = await dbsafe.run_db(_load_for_edit, alert_pk)
    if payload is None:
        return
    channel_id, message_id, embed, actions_and_labels = payload
    view = _make_view(actions_and_labels)

    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning("Cannot refresh alert %s: channel %s is not visible", alert_pk, channel_id)
        return
    try:
        message = await channel.fetch_message(message_id)
    except discord.NotFound:
        logger.info("Alert %s message was deleted in Discord; nothing to refresh", alert_pk)
        return
    await message.edit(embed=discord.Embed.from_dict(embed), view=view)


# -- synchronous halves, run in a worker thread -----------------------------


def _load_for_post(alert_pk):
    settings = AltCorpSettings.current()
    alert = Alert.objects.filter(pk=alert_pk).prefetch_related("facets").first()
    if alert is None or not settings.discord_channel_id:
        return None
    # Non-actionable alerts are posted without a view, per the spec.
    available = actions.available_actions(alert)
    labels = {action: actions.action_label(alert, action) for action in available}
    return (
        int(settings.discord_channel_id),
        embeds.alert_embed(alert),
        _view_data(alert.pk, available, labels),
    )


def _load_for_edit(alert_pk):
    alert = Alert.objects.filter(pk=alert_pk).prefetch_related("facets").first()
    if alert is None or not alert.discord_message_id or not alert.discord_channel_id:
        return None
    available = actions.available_actions(alert)
    labels = {action: actions.action_label(alert, action) for action in available}
    return (
        int(alert.discord_channel_id),
        int(alert.discord_message_id),
        embeds.alert_embed(alert),
        _view_data(alert.pk, available, labels),
    )


def _view_data(alert_pk, available, labels):
    """Serialize view inputs so py-cord objects are made on the bot loop."""
    return tuple(
        (actions.encode_custom_id(alert_pk, action), labels[action]) for action in available
    )


def _make_view(data):
    if not data:
        return None
    alert_pk = int(data[0][0].rsplit(":", 1)[1])
    labels = {
        next(
            action for action in taxonomy.ActionType
            if actions.encode_custom_id(alert_pk, action) == custom_id
        ): label
        for custom_id, label in data
    }
    return AlertView(alert_pk, available=labels, labels=labels)


def _record_message(alert_pk, channel_id, message_id):
    Alert.objects.filter(pk=alert_pk).update(
        discord_channel_id=channel_id, discord_message_id=message_id
    )
