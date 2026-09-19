from django.db import migrations, models


def increase_alert_batch_size(apps, schema_editor):
    apps.get_model("aa_altcorp", "AltCorpSettings").objects.filter(alert_batch_size=10).update(
        alert_batch_size=200
    )


class Migration(migrations.Migration):
    dependencies = [("aa_altcorp", "0004_alert_engine")]

    operations = [
        migrations.AlterField(
            model_name="altcorpsettings",
            name="alert_batch_size",
            field=models.PositiveSmallIntegerField(
                default=200,
                help_text="Alerts delivered per batch, to stay inside Discord rate limits",
            ),
        ),
        migrations.RunPython(increase_alert_batch_size, migrations.RunPython.noop),
    ]
