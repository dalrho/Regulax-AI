from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.documents.preparation import retrieve_evidence
from apps.workspaces.models import Membership


class Command(BaseCommand):
    help = 'Search selected prepared SOP versions and print source-located passages.'

    def add_arguments(self, parser):
        parser.add_argument('query')
        parser.add_argument('--user-id', type=int, required=True)
        parser.add_argument('--workspace-id', type=int, required=True)
        parser.add_argument('--version-ids', type=int, nargs='+', required=True)
        parser.add_argument('--top-k', type=int, default=5)
        parser.add_argument('--neighbors', type=int, default=1)

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(pk=options['user_id']).first()
        membership = Membership.objects.select_related('workspace').filter(
            user=user, workspace_id=options['workspace_id'],
        ).first() if user else None
        if membership is None:
            raise CommandError('User is not a member of the requested workspace.')
        try:
            result = retrieve_evidence(
                user=user,
                workspace=membership.workspace,
                selected_version_ids=options['version_ids'],
                query=options['query'],
                top_k=options['top_k'],
                neighbor_chunks=options['neighbors'],
            )
        except Exception as error:
            raise CommandError(str(error)) from error
        if not result.semantic_available:
            self.stderr.write(self.style.WARNING(
                f'Semantic retrieval unavailable; returning keyword matches only. {result.semantic_warning}'
            ))
        for rank, evidence in enumerate(result.results, 1):
            chunk = evidence.chunk
            self.stdout.write(
                f'[{rank}] chunk={chunk.pk} version={chunk.sop_version_id} '
                f'sop={chunk.sop_version.sop_id} {chunk.sop_version.sop.title!r} '
                f'chunk_index={chunk.chunk_index} chars=[{evidence.passage_start},{evidence.passage_end}) '
                f'matched={",".join(evidence.matched_by)} score={evidence.score:.6f}'
            )
            self.stdout.write(evidence.passage_text)
            self.stdout.write('')
