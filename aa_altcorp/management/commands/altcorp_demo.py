"""Create deterministic local data for testing the plugin without EVE access."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from aa_altcorp.models import AltCorporation, AltCorpReview


class Command(BaseCommand):
    help = "Create local demo users, corporations, and review states for aa-altcorp."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Replace previously created demo records.",
        )

    def handle(self, *args, **options):
        user_model = get_user_model()
        if options["reset"]:
            AltCorporation.objects.filter(source="demo").delete()
            user_model.objects.filter(username__startswith="altcorp-demo-").delete()

        # Deliberately does not touch AltCorpSettings. An earlier version reset
        # the standing target and approved states here, which silently disabled
        # the audit on any install where this was run by mistake.
        examples = (
            ("approved", "Member", True),
            ("wrong-state", "Trial", False),
            ("unreviewed", "Member", None),
        )
        created = []
        for suffix, state, approved in examples:
            user, _ = user_model.objects.get_or_create(
                username=f"altcorp-demo-{suffix}",
                defaults={"email": f"{suffix}@altcorp-demo.invalid"},
            )
            relationship, _ = AltCorporation.objects.get_or_create(
                user=user,
                corporation_id=990000000 + len(created),
                defaults={
                    "corporation_name": f"Demo {suffix.replace('-', ' ').title()} Corporation",
                    "source": "demo",
                },
            )
            review, _ = AltCorpReview.objects.get_or_create(relationship=relationship)
            review.member_state = state
            review.approved = approved
            review.aa_contact_found = approved
            review.standing = 5 if approved else None
            review.reason = "demo approved" if approved else "demo review requires attention"
            review.save()
            created.append(relationship)

        self.stdout.write(self.style.SUCCESS(f"Created {len(created)} demo relationships."))
        self.stdout.write("Alt Corp settings were left untouched.")
        self.stdout.write(
            "Grant aa_altcorp.basic_access and aa_altcorp.manage_relationships to inspect them."
        )
