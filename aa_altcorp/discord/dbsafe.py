"""Run Django ORM work from inside the bot's event loop, safely.

aadiscordbot's house style is plain synchronous ORM inside coroutines, enabled
by the ``DJANGO_ALLOW_ASYNC_UNSAFE`` side effect of importing
``aadiscordbot.cogs.utils.decorators``.  Its ``bot.py`` calls
``close_old_connections()`` around commands and queue tasks, but **not** around
component interactions -- so a button handler can pick up a MySQL connection
the server closed hours ago and fail with "MySQL server has gone away".

Running the query in a worker thread also keeps a slow database from stalling
the bot's heartbeat, and removes any dependence on
``DJANGO_ALLOW_ASYNC_UNSAFE`` for our own code.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_db(func, *args, **kwargs):
    """Await ``func(*args, **kwargs)`` off the event loop, with fresh connections."""

    def _call():
        from django.db import close_old_connections

        close_old_connections()
        try:
            return func(*args, **kwargs)
        finally:
            close_old_connections()

    return await asyncio.to_thread(_call)
