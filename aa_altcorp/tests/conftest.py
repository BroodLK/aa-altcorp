"""Keeps the Alliance Auth integration tests out of the pytest run.

``test_views.py`` needs Alliance Auth in INSTALLED_APPS, which only
``testauth.settings`` provides, so under ``tests.settings`` every test in it
would skip anyway.  It has to be left uncollected rather than merely skipped:
its ``django.test.TestCase`` classes change the order pytest-django applies
transactional fixtures in, and the threaded ``dbsafe.run_db`` tests in
``tests/test_bot_side.py`` then fail with "database table is locked" -- the
exact hazard the ``bot_db`` fixture in ``tests/conftest.py`` documents.

The Django test runner picks the module up normally, which is where it belongs.
"""

collect_ignore = ["test_views.py"]
