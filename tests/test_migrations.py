"""The migration state must match the models.

This gate did not pass before the alert engine landed: ``General`` had never
been written into a migration and ``AltCorpSettings.Meta.verbose_name`` had
drifted, so ``makemigrations --check`` always reported changes.  Migration 0004
absorbed both.  Keeping the check in the suite stops that drift returning.
"""

import io

from django.core.management import call_command


def test_no_model_changes_are_missing_a_migration(db):
    # The db fixture is required: makemigrations checks migration history
    # against the database before comparing models.
    out = io.StringIO()

    call_command("makemigrations", "aa_altcorp", "--check", "--dry-run", stdout=out, verbosity=1)

    assert "No changes detected" in out.getvalue()
