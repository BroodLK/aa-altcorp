"""Buttons attached to an alert message.

Bot-side only: imports py-cord, so nothing in Alliance Auth may import this.

The buttons carry **no callbacks**.  Dispatch happens in the cog's
``on_interaction`` listener, keyed on the ``custom_id``, because that is the
only mechanism that survives a bot restart without registering one persistent
view per open alert.  See ``aa_altcorp.cogs.altcorp_alerts``.
"""

import discord

from ..alerts import taxonomy
from . import actions

BUTTON_STYLES = {
    taxonomy.ActionType.MARK_EXEMPT: discord.ButtonStyle.secondary,
    taxonomy.ActionType.TEMPORARY_BLUE: discord.ButtonStyle.primary,
    taxonomy.ActionType.TEMPORARY_ACL_EXEMPTION: discord.ButtonStyle.primary,
    taxonomy.ActionType.REMOVE_EXEMPTION: discord.ButtonStyle.success,
}


class AlertView(discord.ui.View):
    """One button per action currently available on the alert."""

    def __init__(self, alert_pk, available=None, labels=None):
        # timeout=None keeps the components alive indefinitely; the cog
        # listener, not a registered view, is what actually handles the clicks.
        super().__init__(timeout=None)
        self.alert_pk = int(alert_pk)
        for action in available or ():
            action = taxonomy.ActionType(action)
            self.add_item(
                discord.ui.Button(
                    label=(labels or {}).get(action, action.label),
                    style=BUTTON_STYLES.get(action, discord.ButtonStyle.secondary),
                    custom_id=actions.encode_custom_id(self.alert_pk, action),
                )
            )

    @classmethod
    def for_alert(cls, alert):
        """Build the view from the alert's currently open facets."""
        available = actions.available_actions(alert)
        labels = {action: actions.action_label(alert, action) for action in available}
        return cls(alert.pk, available=available, labels=labels)
