"""The bot-side modules, exercised against a stubbed py-cord.

These are the pieces the plan's partial-suppression decision actually lands in:
the modal re-renders the message narrowed, and only shows RESOLVED IN AUTH once
every facet is covered.
"""

import asyncio
import importlib

import pytest

from aa_altcorp.alerts import taxonomy

BOTH_FACETS = (
    (taxonomy.FacetType.CONTACT, None, ""),
    (taxonomy.FacetType.ACL, 7001, "Capital Staging"),
)


class FakeResponse:
    def __init__(self):
        self.edits = []
        self.messages = []
        self.modals = []

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)

    async def send_message(self, content, **kwargs):
        self.messages.append((content, kwargs))

    async def send_modal(self, modal):
        self.modals.append(modal)


class FakeUser:
    def __init__(self, user_id=123456789, role_ids=(4242,), display_name="SomeAdmin"):
        self.id = user_id
        self.display_name = display_name
        self.roles = [type("Role", (), {"id": rid})() for rid in role_ids]


class FakeInteraction:
    def __init__(self, custom_id=None, role_ids=(4242,)):
        self.data = {"custom_id": custom_id} if custom_id else {}
        self.user = FakeUser(role_ids=role_ids)
        self.guild_id = 999
        self.response = FakeResponse()


def _modals(stub_discord):
    return importlib.import_module("aa_altcorp.discord.modals")


def _fill(modal, reason, expiry=""):
    """Populate the stubbed InputText items the modal created."""
    modal.children[0].value = reason
    if len(modal.children) > 1:
        modal.children[1].value = expiry
    return modal


# -- the modal --------------------------------------------------------------


def test_the_modal_asks_for_a_reason_and_an_expiry(
    alert_settings, make_alert, stub_discord, bot_db
):
    modals = _modals(stub_discord)
    alert = make_alert(facets=BOTH_FACETS)

    modal = modals.ActionModal(alert.pk, taxonomy.ActionType.TEMPORARY_BLUE)

    assert [item.label for item in modal.children] == ["Reason", "Expires in"]
    assert modal.children[0].required is True
    assert modal.children[1].required is True
    assert modal.children[1].value == "30d"


def test_mark_exempt_leaves_the_expiry_optional(alert_settings, make_alert, stub_discord, bot_db):
    modals = _modals(stub_discord)
    alert = make_alert()

    modal = modals.ActionModal(alert.pk, taxonomy.ActionType.MARK_EXEMPT)

    assert modal.children[1].required is False


def test_remove_exemption_asks_only_for_a_reason(alert_settings, make_alert, stub_discord, bot_db):
    modals = _modals(stub_discord)
    alert = make_alert()

    modal = modals.ActionModal(alert.pk, taxonomy.ActionType.REMOVE_EXEMPTION)

    assert [item.label for item in modal.children] == ["Reason"]


def test_a_partial_action_narrows_the_message(alert_settings, make_alert, stub_discord, bot_db):
    """Temporary Blue on a two-facet alert must leave the ACL problem visible."""
    modals = _modals(stub_discord)
    alert = make_alert(facets=BOTH_FACETS)
    modal = _fill(
        modals.ActionModal(alert.pk, taxonomy.ActionType.TEMPORARY_BLUE), "Trial blue", "7d"
    )
    interaction = FakeInteraction()

    asyncio.run(modal.callback(interaction))

    edit = interaction.response.edits[0]
    assert edit["embed"]["title"] != "RESOLVED IN AUTH"
    assert [item.label for item in edit["view"].children] == [
        "Mark Exempt",
        "Temporary ACL exemption",
        "Remove exemption",
    ]
    alert.refresh_from_db()
    assert alert.state == taxonomy.AlertState.OPEN


def test_covering_everything_marks_the_message_resolved(
    alert_settings, make_alert, stub_discord, bot_db
):
    modals = _modals(stub_discord)
    alert = make_alert(facets=BOTH_FACETS)
    modal = _fill(modals.ActionModal(alert.pk, taxonomy.ActionType.MARK_EXEMPT), "Approved")
    interaction = FakeInteraction()

    asyncio.run(modal.callback(interaction))

    edit = interaction.response.edits[0]
    assert edit["embed"]["title"] == "RESOLVED IN AUTH"
    assert edit["view"] is None
    alert.refresh_from_db()
    assert alert.state == taxonomy.AlertState.SUPPRESSED


def test_the_modal_rechecks_authorization(alert_settings, make_alert, stub_discord, bot_db):
    """A modal submit is a fresh interaction, so the role check runs again."""
    from aa_altcorp.models import Exemption

    modals = _modals(stub_discord)
    alert = make_alert()
    modal = _fill(modals.ActionModal(alert.pk, taxonomy.ActionType.MARK_EXEMPT), "Approved")
    interaction = FakeInteraction(role_ids=(1,))

    asyncio.run(modal.callback(interaction))

    assert "do not hold a Discord role" in interaction.response.messages[0][0]
    assert interaction.response.edits == []
    assert Exemption.objects.count() == 0


def test_a_bad_expiry_writes_nothing(alert_settings, make_alert, stub_discord, bot_db):
    from aa_altcorp.models import Exemption

    modals = _modals(stub_discord)
    alert = make_alert()
    modal = _fill(
        modals.ActionModal(alert.pk, taxonomy.ActionType.TEMPORARY_BLUE), "Trial", "soonish"
    )
    interaction = FakeInteraction()

    asyncio.run(modal.callback(interaction))

    assert "Could not read" in interaction.response.messages[0][0]
    assert Exemption.objects.count() == 0


def test_a_deleted_alert_is_reported_not_crashed(alert_settings, make_alert, stub_discord, bot_db):
    modals = _modals(stub_discord)
    modal = _fill(modals.ActionModal(999999, taxonomy.ActionType.MARK_EXEMPT), "Approved")
    interaction = FakeInteraction()

    asyncio.run(modal.callback(interaction))

    assert "no longer exists" in interaction.response.messages[0][0]


# -- the cog listener -------------------------------------------------------


def _cog(stub_discord, monkeypatch):
    import sys
    from types import SimpleNamespace

    ext = SimpleNamespace(
        commands=SimpleNamespace(
            Cog=type("Cog", (), {"listener": staticmethod(lambda *a, **k: lambda f: f)}),
        )
    )
    monkeypatch.setitem(sys.modules, "discord.ext", ext)
    monkeypatch.setitem(sys.modules, "discord.ext.commands", ext.commands)
    monkeypatch.delitem(sys.modules, "aa_altcorp.cogs.altcorp_alerts", raising=False)
    return importlib.import_module("aa_altcorp.cogs.altcorp_alerts")


def test_the_listener_ignores_other_buttons(alert_settings, stub_discord, monkeypatch, bot_db):
    cog_module = _cog(stub_discord, monkeypatch)
    cog = cog_module.AltcorpAlerts(bot=None)
    interaction = FakeInteraction(custom_id="someone-elses-button")

    asyncio.run(cog.handle_alert_button(interaction))

    assert interaction.response.modals == []
    assert interaction.response.messages == []


def test_the_listener_opens_the_modal_for_an_authorized_user(
    alert_settings, make_alert, stub_discord, monkeypatch, bot_db
):
    from aa_altcorp.discord import actions

    cog_module = _cog(stub_discord, monkeypatch)
    cog = cog_module.AltcorpAlerts(bot=None)
    alert = make_alert(facets=BOTH_FACETS)
    custom_id = actions.encode_custom_id(alert.pk, taxonomy.ActionType.MARK_EXEMPT)

    interaction = FakeInteraction(custom_id=custom_id)
    asyncio.run(cog.handle_alert_button(interaction))

    assert len(interaction.response.modals) == 1
    assert interaction.response.modals[0].init_kwargs["title"] == "Mark Both Exempt"


def test_the_listener_denies_an_unauthorized_user(
    alert_settings, make_alert, stub_discord, monkeypatch, bot_db
):
    from aa_altcorp.discord import actions

    cog_module = _cog(stub_discord, monkeypatch)
    cog = cog_module.AltcorpAlerts(bot=None)
    alert = make_alert()
    custom_id = actions.encode_custom_id(alert.pk, taxonomy.ActionType.MARK_EXEMPT)

    interaction = FakeInteraction(custom_id=custom_id, role_ids=(1,))
    asyncio.run(cog.handle_alert_button(interaction))

    assert interaction.response.modals == []
    assert "do not hold a Discord role" in interaction.response.messages[0][0]


def test_a_dm_interaction_has_no_roles_and_is_denied(
    alert_settings, make_alert, stub_discord, monkeypatch, bot_db
):
    from aa_altcorp.discord import actions

    cog_module = _cog(stub_discord, monkeypatch)
    cog = cog_module.AltcorpAlerts(bot=None)
    alert = make_alert()
    interaction = FakeInteraction(
        custom_id=actions.encode_custom_id(alert.pk, taxonomy.ActionType.MARK_EXEMPT)
    )
    del interaction.user.roles  # A DM member object carries no roles.

    asyncio.run(cog.handle_alert_button(interaction))

    assert interaction.response.modals == []


# -- bot_functions ----------------------------------------------------------


class FakeChannel:
    def __init__(self, message_id=555000111):
        self.sent = []
        self.message_id = message_id
        self.fetched = {}

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return type("Message", (), {"id": self.message_id})()

    async def fetch_message(self, message_id):
        return self.fetched[message_id]


class FakeBot:
    def __init__(self, channel=None):
        self.channel = channel

    def get_channel(self, channel_id):
        return self.channel


def _bot_functions(stub_discord, monkeypatch):
    import sys

    monkeypatch.delitem(sys.modules, "aa_altcorp.discord.bot_functions", raising=False)
    return importlib.import_module("aa_altcorp.discord.bot_functions")


def test_posting_records_the_message_id(
    alert_settings, make_alert, stub_discord, monkeypatch, bot_db
):
    """Without the id, the non-interactive edit path could never work."""
    bot_functions = _bot_functions(stub_discord, monkeypatch)
    alert_settings.discord_channel_id = 555
    alert_settings.save()
    alert = make_alert(facets=BOTH_FACETS)
    channel = FakeChannel()

    asyncio.run(bot_functions.post_alert(FakeBot(channel), alert.pk))

    alert.refresh_from_db()
    assert alert.discord_message_id == channel.message_id
    assert alert.discord_channel_id == 555
    assert channel.sent[0]["view"].children


def test_an_invisible_channel_raises_rather_than_going_quiet(
    alert_settings, make_alert, stub_discord, monkeypatch, bot_db
):
    bot_functions = _bot_functions(stub_discord, monkeypatch)
    alert_settings.discord_channel_id = 555
    alert_settings.save()
    alert = make_alert()

    with pytest.raises(RuntimeError, match="not visible"):
        asyncio.run(bot_functions.post_alert(FakeBot(None), alert.pk))


def test_a_resolved_alert_posts_without_buttons(
    alert_settings, make_alert, stub_discord, monkeypatch, bot_db
):
    from django.utils import timezone

    bot_functions = _bot_functions(stub_discord, monkeypatch)
    alert_settings.discord_channel_id = 555
    alert_settings.save()
    alert = make_alert()
    alert.facets.update(state=taxonomy.AlertState.RESOLVED, resolved_at=timezone.now())
    alert.recompute_state()
    channel = FakeChannel()

    asyncio.run(bot_functions.post_alert(FakeBot(channel), alert.pk))

    assert channel.sent[0]["view"] is None


def test_editing_a_deleted_message_is_survivable(
    alert_settings, make_alert, stub_discord, monkeypatch, bot_db
):
    bot_functions = _bot_functions(stub_discord, monkeypatch)
    alert = make_alert()
    alert.discord_channel_id = 555
    alert.discord_message_id = 111
    alert.save()

    class Gone(FakeChannel):
        async def fetch_message(self, message_id):
            raise stub_discord.NotFound()

    # No exception: someone deleting the message must not break the refresh task.
    asyncio.run(bot_functions.edit_alert_message(FakeBot(Gone()), alert.pk))
