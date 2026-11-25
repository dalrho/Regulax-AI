import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.assessments.finding_service import assess_requirement
from apps.assessments.models import Assessment
from apps.assessments.providers import DeterministicProvider, OpenAICompatibleProvider
from apps.regulations.models import Requirement
from apps.workspaces.models import Membership


class Command(BaseCommand):
    help = 'Assess and save exactly one requirement for a draft/frozen assessment from the command line.'

    def add_arguments(self, parser):
        parser.add_argument('assessment_id', type=int)
        parser.add_argument('requirement_id', type=int)
        parser.add_argument('--user-id', type=int, required=True)
        parser.add_argument('--provider', choices=('deterministic', 'openai'), default='deterministic')

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(pk=options['user_id']).first()
        assessment = Assessment.objects.select_related('workspace', 'pack_version').filter(
            pk=options['assessment_id'],
        ).first()
        if user is None or assessment is None or not Membership.objects.filter(
            user=user, workspace=assessment.workspace,
        ).exists():
            raise CommandError('User or assessment is unavailable in the requested workspace.')
        requirement = Requirement.objects.filter(
            pk=options['requirement_id'], pack_version=assessment.pack_version,
        ).first()
        if requirement is None:
            raise CommandError('Requirement is not in the assessment’s pinned pack version.')
        provider = OpenAICompatibleProvider() if options['provider'] == 'openai' else DeterministicProvider()
        try:
            finding = assess_requirement(assessment=assessment, requirement=requirement, provider=provider)
        except Exception as error:
            raise CommandError(f'{type(error).__name__}: {error}') from error
        self.stdout.write(json.dumps({
            'finding_id': finding.pk,
            'assessment_id': assessment.pk,
            'requirement_id': requirement.pk,
            'category': finding.category,
            'provider': finding.provider_name,
            'model': finding.model_name,
            'review_scope': finding.review_scope,
            'saved_evidence_count': finding.evidence.count(),
            'assessment_status': assessment.status,
            'note': (
                'Deterministic development provider; no substantive provider execution was performed.'
                if options['provider'] == 'deterministic'
                else 'Live provider execution was requested.'
            ),
        }, ensure_ascii=False, indent=2))
