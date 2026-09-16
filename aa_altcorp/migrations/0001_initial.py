from datetime import timedelta

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="AltCorpSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("approved_states",
                 models.JSONField(default=list, help_text="Alliance Auth member states accepted by the audit")),
                ("standing_target_type",
                 models.CharField(choices=[("alliance", "Alliance"), ("corporation", "Corporation")],
                                  default="alliance", max_length=12)),
                ("standing_target_id", models.BigIntegerField(blank=True, null=True)),
                ("standing_target_name", models.CharField(blank=True, max_length=255)),
                ("notification_interval", models.DurationField(default=timedelta(days=1))),
                ("webhook_url", models.URLField(blank=True)),
                ("enabled", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="AltCorporation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("corporation_id", models.BigIntegerField()),
                ("corporation_name", models.CharField(max_length=255)),
                ("source", models.CharField(default="esi", max_length=20)),
                ("attached_at", models.DateTimeField(auto_now_add=True)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="alt_corporations",
                                           to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("corporation_name",)},
        ),
        migrations.CreateModel(
            name="AltCorpReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("approved", models.BooleanField(null=True)),
                ("member_state", models.CharField(blank=True, max_length=100)),
                ("standing", models.FloatField(blank=True, null=True)),
                ("aa_contact_found", models.BooleanField(null=True)),
                ("reason", models.TextField(blank=True)),
                ("checked_at", models.DateTimeField(blank=True, null=True)),
                ("first_notified_at", models.DateTimeField(blank=True, null=True)),
                ("last_notified_at", models.DateTimeField(blank=True, null=True)),
                ("relationship",
                 models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="review",
                                      to="aa_altcorp.altcorporation")),
            ],
        ),
        migrations.AddConstraint(model_name="altcorporation",
                                 constraint=models.UniqueConstraint(fields=("user", "corporation_id"),
                                                                    name="unique_user_alt_corp")),
    ]
