from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.documents.models import SOPVersion
from apps.documents.preparation import PreparationError, prepare_sop_version
from apps.workspaces.models import Membership


class Command(BaseCommand):
    help = 'Chunk and embed SOP versions for evidence retrieval.'

    def add_arguments(self, parser):
        parser.add_argument('version_ids', nargs='+', type=int)
        parser.add_argument('--user-id', type=int, required=True, help='Workspace member requesting preparation.')

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(pk=options['user_id']).first()
        if user is None:
            raise CommandError('User not found.')
        for version_id in options['version_ids']:
            version = SOPVersion.objects.select_related('sop__workspace').filter(pk=version_id).first()
            if version is None:
                raise CommandError(f'SOP version {version_id} was not found.')
            workspace = version.sop.workspace
            if not Membership.objects.filter(user=user, workspace=workspace).exists():
                raise CommandError(f'User {user.pk} is not a member of the workspace owning version {version_id}.')
            try:
                prepared = prepare_sop_version(user=user, workspace=workspace, version_id=version_id)
            except Exception as error:
                raise CommandError(f'Version {version_id}: {error}') from error
            self.stdout.write(self.style.SUCCESS(
                f'Version {prepared.pk} {prepared.version_label}: {prepared.processing_status}; '
                f'model={prepared.embedding_model}; dimensions={prepared.embedding_dimensions}; '
                f'chunks={prepared.chunks.filter(preparation_key=prepared.active_preparation_key).count()}'
            ))
