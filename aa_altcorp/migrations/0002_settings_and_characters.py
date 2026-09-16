from django.db import migrations, models
from django.conf import settings
import django.db.models.deletion

import aa_altcorp.models


class Migration(migrations.Migration):
    dependencies = [("aa_altcorp", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="AltCharacter",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("character_id", models.BigIntegerField()),
                ("character_name", models.CharField(max_length=255)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="alt_characters", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("character_name",)},
        ),
        migrations.AddConstraint(
            model_name="altcharacter",
            constraint=models.UniqueConstraint(fields=("user", "character_id"), name="unique_user_alt_character"),
        ),
        migrations.CreateModel(
            name="CharacterAccessList",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("character_id", models.BigIntegerField()),
                ("access_list_id", models.BigIntegerField()),
                ("name", models.CharField(blank=True, max_length=255)),
                ("description", models.TextField(blank=True)),
                ("membership", models.JSONField(default=dict)),
                ("synced_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="CharacterAccessToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("character_id", models.BigIntegerField(unique=True)),
                ("token_id", models.BigIntegerField(unique=True)),
                ("added_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.AddConstraint(
            model_name="characteraccesslist",
            constraint=models.UniqueConstraint(fields=("character_id", "access_list_id"), name="unique_character_access_list"),
        ),
        migrations.RemoveField(model_name="altcorpsettings", name="standing_target_name"),
        migrations.AlterField(
            model_name="altcorpsettings",
            name="approved_states",
            field=models.JSONField(default=aa_altcorp.models.default_approved_states, help_text="Alliance Auth member states accepted by the audit"),
        ),
        migrations.AlterField(
            model_name="altcorpsettings",
            name="notification_interval",
            field=models.CharField(default="0 0 * * *", max_length=100),
        ),
    ]
