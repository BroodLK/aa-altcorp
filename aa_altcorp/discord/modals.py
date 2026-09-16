"""The reason/expiry modal behind every alert button.

Bot-side only: imports py-cord.

Deviation from the original spec, deliberate: it mocks the expiry up as a row
of buttons (``[1 Day] [3 Days] [7 Days] [Custom]``).  **Discord modals cannot
contain buttons.**  The alternative -- an ephemeral button message followed by
a separate reason modal -- costs two round trips and two interaction tokens, so
this uses one modal with a parsed free-text expiry and the spec's values as the
placeholder.
"""

import logging

import discord

from ..alerts import taxonomy
from . import actions, dbsafe, embeds

logger = logging.getLogger(__name__)

EXPIRY_PLACEHOLDER = actions.EXPIRY_HELP


class ActionModal(discord.ui.Modal):
    """Collects the reason, and an expiry when the action needs one."""

    def __init__(self, alert_pk, action, title=None):
        self.alert_pk = int(alert_pk)
        self.action = taxonomy.ActionType(action)
        super().__init__(title=(title or self.action.label)[:45])

        self.add_item(
            discord.ui.InputText(
                label="Reason",
                style=discord.InputTextStyle.long,
                required=True,
                max_length=400,
                placeholder="Why is this the right call? Someone will read this in six months.",
            )
        )
        if self.action is not taxonomy.ActionType.REMOVE_EXEMPTION:
            required = self.action in taxonomy.EXPIRY_REQUIRED
            self.add_item(
                discord.ui.InputText(
                    label="Expires in",
                    style=discord.InputTextStyle.short,
                    required=required,
                    value="30d" if required else "",
                    placeholder=EXPIRY_PLACEHOLDER,
                )
            )

    @property
    def reason(self):
        return self.children[0].value or ""

    @property
    def expiry_text(self):
        return self.children[1].value if len(self.children) > 1 else ""

    async def callback(self, interaction):
        # A modal submit is a fresh interaction, so re-check authorization
        # rather than trusting that the button click was allowed.
        role_ids = {role.id for role in getattr(interaction.user, "roles", ())}
        if not await dbsafe.run_db(actions.is_authorized, role_ids):
            await interaction.response.send_message(
                "You do not hold a Discord role permitted to action these alerts.",
                ephemeral=True,
            )
            return

        try:
            alert, log = await dbsafe.run_db(
                _apply,
                self.alert_pk,
                self.action,
                self.reason,
                self.expiry_text,
                interaction.user.id,
                getattr(interaction.user, "display_name", "") or str(interaction.user),
                interaction.guild_id,
            )
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        except Exception:
            logger.exception("Failed to apply %s to alert %s", self.action, self.alert_pk)
            await interaction.response.send_message(
                "Something went wrong applying that action. Nothing was changed.",
                ephemeral=True,
            )
            return

        await _rerender(interaction, alert, log)


def _apply(alert_pk, action, reason, expiry_text, discord_id, discord_name, guild_id):
    """Synchronous half, run in a worker thread by :func:`dbsafe.run_db`."""
    alert = actions.alert_for(alert_pk)
    if alert is None:
        raise ValueError("That alert no longer exists.")
    actor = actions.Actor(
        discord_id=discord_id,
        discord_name=discord_name,
        guild_id=guild_id,
        user=actions.resolve_actor_user(discord_id),
    )
    if action is taxonomy.ActionType.REMOVE_EXEMPTION:
        log = actions.remove_exemption(alert, reason, actor)
    else:
        _, log = actions.apply_action(alert, action, reason, expiry_text, actor)
    alert.refresh_from_db()
    return alert, log


async def _rerender(interaction, alert, log):
    """Narrow the message, or mark it resolved once nothing is left open.

    Editing through the interaction needs no stored message id and works even
    if the bot restarted since the alert was posted.
    """
    from .views import AlertView

    still_open = await dbsafe.run_db(lambda: bool(embeds.open_facets(alert)))
    if still_open:
        embed, view = await dbsafe.run_db(
            lambda: (embeds.alert_embed(alert), AlertView.for_alert(alert))
        )
        await interaction.response.edit_message(embed=discord.Embed.from_dict(embed), view=view)
        return

    embed = await dbsafe.run_db(embeds.resolved_embed, alert, log)
    await interaction.response.edit_message(embed=discord.Embed.from_dict(embed), view=None)
