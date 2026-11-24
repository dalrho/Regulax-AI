from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.workspaces.models import Membership
from apps.workspaces.services import ensure_personal_workspace


class Command(BaseCommand):
    help = 'Create a private owner workspace for each existing account without a membership.'

    def handle(self, *args, **options):
        User = get_user_model()
        created = 0
        for user in User.objects.iterator():
            had_membership = Membership.objects.filter(user=user).exists()
            ensure_personal_workspace(user)
            created += not had_membership
        self.stdout.write(self.style.SUCCESS(f'Workspace backfill complete. Created {created} workspace(s).'))
