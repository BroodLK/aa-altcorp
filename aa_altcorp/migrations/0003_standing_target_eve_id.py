"""Convert standing targets stored as Alliance Auth row pks into EVE ids."""

from django.db import migrations, models


def forwards(apps, schema_editor):
    Settings = apps.get_model("aa_altcorp", "AltCorpSettings")
    stale = Settings.objects.exclude(standing_target_id=None)
    if not stale.exists():
        # Nothing to convert, so never touch the eveonline tables. This also keeps
        # a fresh install safe when eveonline's own migrations have yet to run.
        return
    try:
        Alliance = apps.get_model("eveonline", "EveAllianceInfo")
        Corporation = apps.get_model("eveonline", "EveCorporationInfo")
    except LookupError:
        # allianceauth.eveonline is not installed, e.g. the plugin test project.
        return
    for config in stale:
        model, id_field = (
            (Alliance, "alliance_id")
            if config.standing_target_type == "alliance"
            else (Corporation, "corporation_id")
        )
        if model.objects.filter(**{id_field: config.standing_target_id}).exists():
            # Already an EVE id, so this migration is safe to run more than once.
            continue
        owner = model.objects.filter(pk=config.standing_target_id).first()
        if owner is None:
            # Neither an EVE id nor a pk; leave it for the admin to re-pick.
            continue
        print(
            f"aa_altcorp: converting standing target {config.standing_target_id} "
            f"to {id_field} {getattr(owner, id_field)}"
        )
        config.standing_target_id = getattr(owner, id_field)
        config.save(update_fields=["standing_target_id"])


class Migration(migrations.Migration):
    dependencies = [("aa_altcorp", "0002_settings_and_characters")]

    operations = [
        migrations.AlterField(
            model_name="altcorpsettings",
            name="standing_target_id",
            field=models.BigIntegerField(
                blank=True,
                help_text="EVE alliance or corporation ID, not the local Alliance Auth row ID",
                null=True,
            ),
        ),
        # Reversing would mean guessing which values were converted, and the EVE id
        # is valid under the old readers anyway.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
