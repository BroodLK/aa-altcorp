"""Alert action cog for allianceauth-discordbot.

Loaded by the bot through the ``discord_cogs_hook`` in
:mod:`aa_altcorp.auth_hooks`.  Never imported by Alliance Auth itself.

**Why a listener rather than persistent views.**  py-cord's ``ViewStore``
dispatches on an exact ``(component_type, message_id, custom_id)`` match, so
``bot.add_view()`` would need one registration per open alert, would grow with
the board, and would miss every alert posted after start-up.  Transient views
expire minutes after delivery, which is useless for an alert someone reads the
next morning.  py-cord 2.8 has no ``DynamicItem``.

But ``discord.state.ConnectionState.parse_interaction_create`` calls
``dispatch("interaction", ...)`` unconditionally -- including for components the
ViewStore has never heard of -- and ``Client.dispatch`` delivers that to cog
listeners as well as to ``AuthBot``'s own ``on_interaction`` override.  So one
listener handles every alert message ever posted, across any number of bot
restarts.  That is why the buttons in ``discord/views.py`` carry no callbacks:
the ``custom_id`` is the entire protocol.
"""

import logging

from discord.ext import commands

from ..alerts import taxonomy
from ..discord import actions, dbsafe
from ..discord.modals import ActionModal

logger = logging.getLogger(__name__)


class AltcorpAlerts(commands.Cog):
    """Handles the buttons on aa-altcorp alert messages."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener("on_interaction")
    async def handle_alert_button(self, interaction):
        data = getattr(interaction, "data", None) or {}
        decoded = actions.decode_custom_id(data.get("custom_id"))
        if decoded is None:
            return  # Not one of ours.
        alert_pk, action = decoded

        # Authorization is by configured Discord role only, as specified.
        # A DM interaction has no .roles, so the default correctly denies.
        role_ids = {role.id for role in getattr(interaction.user, "roles", ())}
        if not await dbsafe.run_db(actions.is_authorized, role_ids):
            await interaction.response.send_message(
                "You do not hold a Discord role permitted to action these alerts.",
                ephemeral=True,
            )
            return

        title = await dbsafe.run_db(_modal_title, alert_pk, action)
        if title is None:
            await interaction.response.send_message("That alert no longer exists.", ephemeral=True)
            return
        await interaction.response.send_modal(ActionModal(alert_pk, action, title=title))


def _modal_title(alert_pk, action):
    alert = actions.alert_for(alert_pk)
    if alert is None:
        return None
    if taxonomy.ActionType(action) is taxonomy.ActionType.REMOVE_EXEMPTION:
        return "Remove Exemption"
    return actions.action_label(alert, action)


def setup(bot):
    # Synchronous add_cog: py-cord style, and AuthBot.__init__ calls
    # load_extension before the event loop exists.
    bot.add_cog(AltcorpAlerts(bot))
