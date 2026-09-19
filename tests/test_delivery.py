"""Delivery routing, the webhook fallback, and embed limits."""

import importlib
from unittest.mock import patch

from aa_altcorp.alerts import taxonomy
from aa_altcorp.discord import delivery, embeds

BOTH_FACETS = (
    (taxonomy.FacetType.CONTACT, None, ""),
    (taxonomy.FacetType.ACL, 7001, "Capital Staging"),
)


def _with_channel(settings, channel_id=555):
    settings.discord_channel_id = channel_id
    settings.save()
    return settings


# -- routing ----------------------------------------------------------------


def test_the_bot_is_preferred_when_available(alert_settings, make_alert, stub_aadiscordbot):
    alert = make_alert(facets=BOTH_FACETS)
    _with_channel(alert_settings)

    assert delivery.deliver_alert(alert, alert_settings) == taxonomy.DeliveryChannel.BOT
    assert len(stub_aadiscordbot) == 1
    queued = stub_aadiscordbot[0]["kwargs"]
    assert queued["args"] == [delivery.POST_ALERT_FUNCTION]
    # Only a primitive pk crosses the JSON boundary.
    assert queued["kwargs"]["task_args"] == [alert.pk]
    alert.refresh_from_db()
    assert alert.notified_at is not None


def test_a_missing_bot_falls_back_to_the_webhook(alert_settings, make_alert, no_aadiscordbot):
    alert = make_alert()
    _with_channel(alert_settings)
    alert_settings.webhook_url = "https://example.test/webhook"
    alert_settings.save()

    with patch("aa_altcorp.services.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b""
        result = delivery.deliver_alert(alert, alert_settings)

    assert result == taxonomy.DeliveryChannel.WEBHOOK


def test_a_dead_broker_falls_back_to_the_webhook(
    alert_settings, make_alert, stub_aadiscordbot, monkeypatch
):
    """apply_async raising must not lose the alert."""
    import sys

    def explode(*args, **kwargs):
        raise OSError("broker unreachable")

    sys.modules["aadiscordbot.tasks"].run_task_function.apply_async = explode
    alert = make_alert()
    _with_channel(alert_settings)
    alert_settings.webhook_url = "https://example.test/webhook"
    alert_settings.save()

    with patch("aa_altcorp.services.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b""
        result = delivery.deliver_alert(alert, alert_settings)

    assert result == taxonomy.DeliveryChannel.WEBHOOK


def test_no_route_is_skipped(alert_settings, make_alert, no_aadiscordbot):
    alert = make_alert()

    assert delivery.deliver_alert(alert, alert_settings) == delivery.SKIPPED
    alert.refresh_from_db()
    assert alert.notified_at is None


def test_delivery_none_skips_everything(alert_settings, make_alert, stub_aadiscordbot):
    alert = make_alert()
    _with_channel(alert_settings)
    alert_settings.alert_delivery = taxonomy.AlertDelivery.NONE
    alert_settings.save()

    assert delivery.deliver_alert(alert, alert_settings) == delivery.SKIPPED
    assert stub_aadiscordbot == []


def test_bot_only_never_falls_back(alert_settings, make_alert, no_aadiscordbot):
    alert = make_alert()
    _with_channel(alert_settings)
    alert_settings.alert_delivery = taxonomy.AlertDelivery.BOT
    alert_settings.webhook_url = "https://example.test/webhook"
    alert_settings.save()

    assert delivery.deliver_alert(alert, alert_settings) == delivery.SKIPPED


def test_a_failing_webhook_is_swallowed(alert_settings, make_alert, no_aadiscordbot):
    """A 500 must not abort the batch; that bug used to kill the audit loop."""
    alert = make_alert()
    alert_settings.webhook_url = "https://example.test/webhook"
    alert_settings.save()

    with patch("aa_altcorp.services.urlopen") as urlopen:
        urlopen.side_effect = OSError("connection refused")
        result = delivery.deliver_alert(alert, alert_settings)

    assert result == delivery.SKIPPED


def test_the_webhook_body_carries_the_alert(alert_settings, make_alert, no_aadiscordbot):
    import json

    alert = make_alert(facets=BOTH_FACETS)
    alert_settings.webhook_url = "https://example.test/webhook"
    alert_settings.save()

    with patch("aa_altcorp.services.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b""
        delivery.deliver_alert(alert, alert_settings)

    body = json.loads(urlopen.call_args[0][0].data)
    assert "Example Corp" in body["content"]
    assert "allianceauth-discordbot" in body["content"]
    assert body["embeds"][0]["title"]


def test_one_bad_alert_does_not_abort_the_batch(alert_settings, make_alert, monkeypatch):
    alerts = [make_alert(entity_id=1), make_alert(entity_id=2)]
    calls = []

    def flaky(alert, settings=None):
        calls.append(alert.entity_id)
        if alert.entity_id == 1:
            raise RuntimeError("boom")
        return taxonomy.DeliveryChannel.BOT

    monkeypatch.setattr(delivery, "deliver_alert", flaky)
    counts = delivery.deliver_alerts(alerts, alert_settings)

    assert calls == [1, 2]
    assert counts[delivery.SKIPPED] == 1


# -- pending selection ------------------------------------------------------


def test_pending_alerts_excludes_already_notified(alert_settings, make_alert):
    from django.utils import timezone

    fresh = make_alert(entity_id=1)
    done = make_alert(entity_id=2)
    done.notified_at = timezone.now()
    done.save()

    assert list(delivery.pending_alerts(alert_settings)) == [fresh]


def test_pending_alerts_excludes_suppressed(alert_settings, make_alert, actor):
    from aa_altcorp.discord import actions

    alert = make_alert()
    actions.apply_action(alert, "MARK_EXEMPT", reason="Approved", actor=actor)

    assert list(delivery.pending_alerts(alert_settings)) == []


# -- embeds -----------------------------------------------------------------


def test_the_embed_lists_one_line_per_open_facet(make_alert):
    alert = make_alert(facets=BOTH_FACETS)

    embed = embeds.alert_embed(alert)

    names = [field["name"] for field in embed["fields"]]
    assert "Contact standing" in names
    assert "ACL Capital Staging" in names


def test_the_embed_hides_suppressed_facets_from_the_open_list(make_alert, actor):
    from aa_altcorp.discord import actions

    alert = make_alert(facets=BOTH_FACETS)
    actions.apply_action(alert, "TEMPORARY_BLUE", reason="Trial", expiry_text="7d", actor=actor)
    alert.refresh_from_db()

    embed = embeds.alert_embed(alert)

    names = [field["name"] for field in embed["fields"]]
    assert "ACL Capital Staging" in names
    assert "Already exempted" in names


def test_embeds_respect_discord_limits(make_alert):
    """An entity on forty ACLs must not produce a silent HTTP 400."""
    facets = tuple((taxonomy.FacetType.ACL, 8000 + i, "L" * 400) for i in range(40))
    alert = make_alert(entity_name="N" * 600, facets=facets)

    embed = embeds.alert_embed(alert)

    assert len(embed["title"]) <= embeds.TITLE_LIMIT
    assert len(embed["description"]) <= embeds.DESCRIPTION_LIMIT
    assert len(embed["fields"]) <= embeds.FIELD_LIMIT
    for entry in embed["fields"]:
        assert len(entry["name"]) <= embeds.FIELD_NAME_LIMIT
        assert len(entry["value"]) <= embeds.FIELD_VALUE_LIMIT
    assert any("further access lists" in f["value"] for f in embed["fields"])


def test_the_resolved_embed_shows_the_spec_fields(make_alert, actor):
    from aa_altcorp.discord import actions

    alert = make_alert(facets=BOTH_FACETS)
    _, log = actions.apply_action(
        alert, "MARK_EXEMPT", reason="Third-party logistics partner", actor=actor
    )

    embed = embeds.resolved_embed(alert, log)

    assert embed["title"].startswith("Resolved: Example Corp")
    names = {field["name"]: field["value"] for field in embed["fields"]}
    assert names["Reason"] == "Third-party logistics partner"
    assert names["Expires"] == "Never"
    assert names["Actioned by"] == "SomeAdmin"
    assert "Nothing was changed in EVE" in embed["description"]


def test_a_very_long_reason_is_clipped(make_alert, actor):
    from aa_altcorp.discord import actions

    alert = make_alert()
    _, log = actions.apply_action(alert, "MARK_EXEMPT", reason="x" * 400, actor=actor)

    embed = embeds.resolved_embed(alert, log)

    assert all(len(f["value"]) <= embeds.FIELD_VALUE_LIMIT for f in embed["fields"])


# -- views ------------------------------------------------------------------


def test_the_view_renders_the_available_actions(make_alert, stub_discord):
    views = importlib.import_module("aa_altcorp.discord.views")
    alert = make_alert(facets=BOTH_FACETS)

    view = views.AlertView.for_alert(alert)

    labels = [item.label for item in view.children]
    assert labels == ["Mark Both Exempt", "Temporary blue", "Temporary ACL exemption"]
    for item in view.children:
        from aa_altcorp.discord import actions

        assert actions.decode_custom_id(item.custom_id)[0] == alert.pk


def test_the_view_gains_remove_once_suppressed(make_alert, stub_discord, actor):
    from aa_altcorp.discord import actions

    views = importlib.import_module("aa_altcorp.discord.views")
    alert = make_alert(facets=BOTH_FACETS)
    actions.apply_action(alert, "TEMPORARY_BLUE", reason="Trial", expiry_text="7d", actor=actor)

    labels = [item.label for item in views.AlertView.for_alert(alert).children]

    assert "Remove exemption" in labels
    assert "Temporary blue" not in labels


def test_a_fully_suppressed_alert_renders_only_remove(make_alert, stub_discord, actor):
    from aa_altcorp.discord import actions

    views = importlib.import_module("aa_altcorp.discord.views")
    alert = make_alert(facets=((taxonomy.FacetType.CONTACT, None, ""),))
    actions.apply_action(alert, "MARK_EXEMPT", reason="Approved", actor=actor)

    labels = [item.label for item in views.AlertView.for_alert(alert).children]

    assert labels == ["Remove exemption"]


def test_a_resolved_alert_renders_no_buttons(make_alert, stub_discord):
    from django.utils import timezone

    views = importlib.import_module("aa_altcorp.discord.views")
    alert = make_alert()
    alert.facets.update(state=taxonomy.AlertState.RESOLVED, resolved_at=timezone.now())
    alert.recompute_state()

    assert views.AlertView.for_alert(alert).children == []
