"""Discord integration.

Split deliberately by import weight:

``actions``, ``delivery``, ``embeds`` and ``dbsafe`` import no Discord library
and run inside Alliance Auth, so they stay importable (and testable) on an
install with no bot.  ``views``, ``modals``, ``bot_functions`` and the cog in
``aa_altcorp.cogs`` import py-cord and are only ever loaded by the bot process.
"""
