from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.documents.models import SOPVersion
from apps.regulations.models import RequirementPackVersion
from apps.workspaces.models import Membership

from .models import Assessment, AssessmentDocument


def _require_membership(user, workspace):
    if not getattr(user, 'is_authenticated', False) or not Membership.objects.filter(user=user, workspace=workspace).exists():
        raise PermissionDenied('Workspace membership is required to access assessments.')


def _positive_id(value, label):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValidationError(f'Choose a valid {label}.') from error
    if parsed < 1:
        raise ValidationError(f'Choose a valid {label}.')
    return parsed


def _validated_scope(*, user, workspace, pack_version_id, primary_version_id, supporting_version_ids):
    _require_membership(user, workspace)
    pack_id = _positive_id(pack_version_id, 'requirement pack version')
    primary_id = _positive_id(primary_version_id, 'primary SOP version')
    support_ids = [_positive_id(value, 'supporting SOP version') for value in supporting_version_ids]
    if len(support_ids) != len(set(support_ids)) or primary_id in support_ids:
        raise ValidationError('Each SOP version can be selected only once.')
    try:
        pack_version = RequirementPackVersion.objects.select_related('pack').get(
            pk=pack_id,
            status=RequirementPackVersion.Status.PUBLISHED,
            is_development_fixture=False,
            pack__is_archived=False,
        )
    except RequirementPackVersion.DoesNotExist as error:
        raise ValidationError('Choose a published version of an active requirement pack.') from error
    versions = list(SOPVersion.objects.select_related('sop').filter(
        pk__in=[primary_id, *support_ids], sop__workspace=workspace, sop__is_archived=False,
    ))
    by_id = {version.pk: version for version in versions}
    missing = set([primary_id, *support_ids]) - set(by_id)
    if missing:
        raise ValidationError('One or more selected SOP versions are not available in this workspace.')
    return pack_version, by_id[primary_id], [by_id[version_id] for version_id in support_ids]


@transaction.atomic
def save_assessment(*, user, workspace, name, pack_version_id, primary_version_id, supporting_version_ids=(), scope_confirmed=False, assessment_id=None):
    """Create or edit a draft while pinning every choice to exact source version IDs."""
    _require_membership(user, workspace)
    name = (name or '').strip()
    if not name:
        raise ValidationError({'name': 'Enter an assessment name.'})
    if len(name) > 200:
        raise ValidationError({'name': 'Assessment name must be 200 characters or fewer.'})
    if not scope_confirmed:
        raise ValidationError('Confirm the selected versions define this assessment scope.')
    pack_version, primary, supporting = _validated_scope(
        user=user, workspace=workspace, pack_version_id=pack_version_id,
        primary_version_id=primary_version_id, supporting_version_ids=supporting_version_ids,
    )
    if assessment_id is None:
        assessment = Assessment.objects.create(
            workspace=workspace, name=name, pack_version=pack_version, created_by=user,
        )
    else:
        try:
            assessment = Assessment.objects.select_for_update().get(
                pk=assessment_id, workspace=workspace, status=Assessment.Status.DRAFT, scope_frozen_at__isnull=True,
            )
        except Assessment.DoesNotExist as error:
            raise PermissionDenied('That draft is unavailable or its scope has started.') from error
        assessment.name = name
        assessment.pack_version = pack_version
        assessment.save(update_fields=('name', 'pack_version', 'updated_at'))
        assessment.documents.all().delete()
    AssessmentDocument.objects.create(assessment=assessment, sop_version=primary, role=AssessmentDocument.Role.PRIMARY)
    AssessmentDocument.objects.bulk_create([
        AssessmentDocument(assessment=assessment, sop_version=version, role=AssessmentDocument.Role.SUPPORTING)
        for version in supporting
    ])
    return assessment


def list_assessments(*, user, workspace):
    _require_membership(user, workspace)
    return Assessment.objects.filter(workspace=workspace).select_related(
        'created_by', 'pack_version__pack',
    ).prefetch_related('documents__sop_version__sop').order_by('-created_at', '-pk')


def get_assessment(*, user, workspace, assessment_id):
    _require_membership(user, workspace)
    try:
        return Assessment.objects.select_related('pack_version__pack', 'created_by').prefetch_related(
            'documents__sop_version__sop',
        ).get(pk=assessment_id, workspace=workspace)
    except Assessment.DoesNotExist as error:
        raise PermissionDenied('That assessment is not in the active workspace.') from error


@transaction.atomic
def freeze_assessment_scope(*, user, workspace, assessment_id):
    """Execution-service hook: freeze the saved definition before a future run starts."""
    _require_membership(user, workspace)
    try:
        assessment = Assessment.objects.select_for_update().get(
            pk=assessment_id, workspace=workspace, status=Assessment.Status.DRAFT, scope_frozen_at__isnull=True,
        )
    except Assessment.DoesNotExist as error:
        raise ValidationError('Only an unstarted draft can be frozen for execution.') from error
    if not assessment.documents.filter(role=AssessmentDocument.Role.PRIMARY).exists():
        raise ValidationError('An assessment must have one primary SOP version before execution.')
    assessment.scope_frozen_at = timezone.now()
    assessment.save(update_fields=('scope_frozen_at', 'updated_at'))
    return assessment
