"""Database-backed queue, leases, retries, exclusions, and worker execution."""

import os
import time
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.documents.embeddings import SentenceTransformersProvider
from apps.documents.models import SOPChunk, SOPVersion
from apps.regulations.models import Requirement, RequirementPackVersion
from apps.workspaces.models import Membership

from .finding_service import (
    FindingAssessmentError,
    RequirementExcluded,
    StaleWorkerError,
    assess_requirement,
)
from .models import (
    Assessment,
    AssessmentDocument,
    AssessmentRunJob,
    Finding,
    RequirementExecution,
    RequirementExecutionFailure,
    RequirementExclusion,
)
from .providers import (
    FINDING_PROMPT_VERSION,
    DeterministicProvider,
    OpenAICompatibleProvider,
)


DEFAULT_LEASE_SECONDS = 300


def _member(user, workspace):
    if not getattr(user, 'is_authenticated', False) or not Membership.objects.filter(
        user=user, workspace=workspace,
    ).exists():
        raise PermissionDenied('Workspace membership is required to run or review assessments.')


def _ready_approved_scope(assessment):
    if assessment.findings.filter(provider_name='deterministic_test_double').exists():
        raise ValidationError(
            'This draft contains a deterministic development finding. Create a new assessment before a live provider run.'
        )
    version = assessment.pack_version
    if (
        version.status != RequirementPackVersion.Status.PUBLISHED
        or version.is_development_fixture
        or version.pack.is_archived
        or version.reviewed_by_id is None
        or version.reviewed_at is None
    ):
        raise ValidationError('Run requires a published, reviewed, approved requirement pack version.')
    requirements = list(version.requirements.order_by('sort_order', 'code', 'pk'))
    if not requirements:
        raise ValidationError('The pinned pack version has no requirements to assess.')
    documents = list(assessment.documents.select_related('sop_version__sop'))
    if not documents or not any(item.role == AssessmentDocument.Role.PRIMARY for item in documents):
        raise ValidationError('Select one primary SOP version before running an assessment.')
    unready = [
        item.sop_version for item in documents
        if item.sop_version.processing_status != SOPVersion.ProcessingStatus.READY
        or not item.sop_version.active_preparation_key
    ]
    if unready:
        names = ', '.join(f'{item.sop.title} {item.version_label}' for item in unready)
        raise ValidationError(f'Prepare every selected SOP version before running: {names}.')
    preparation_keys = {}
    embedding_configs = set()
    for item in documents:
        preparation_key = item.preparation_key or item.sop_version.active_preparation_key
        chunk_configs = set(SOPChunk.objects.filter(
            sop_version=item.sop_version, preparation_key=preparation_key,
        ).values_list('embedding_model', 'embedding_dimensions').distinct())
        if not preparation_key or not chunk_configs:
            raise ValidationError(f'{item.sop_version.sop.title} {item.sop_version.version_label} has no active evidence chunks.')
        if len(chunk_configs) != 1:
            raise ValidationError(f'{item.sop_version.sop.title} {item.sop_version.version_label} has inconsistent evidence chunk configuration.')
        preparation_keys[str(item.sop_version_id)] = preparation_key
        embedding_configs.update(chunk_configs)
    if len(embedding_configs) != 1:
        raise ValidationError('Prepare all selected SOP versions with the same embedding model before running.')
    embedding_model, embedding_dimensions = next(iter(embedding_configs))
    return requirements, preparation_keys, embedding_model, embedding_dimensions


@transaction.atomic
def queue_assessment(*, user, workspace, assessment_id):
    """Freeze a complete approved scope and persist its durable job/accounting rows."""
    _member(user, workspace)
    try:
        assessment = Assessment.objects.select_for_update().select_related('pack_version__pack').get(
            pk=assessment_id, workspace=workspace,
        )
    except Assessment.DoesNotExist as error:
        raise PermissionDenied('That assessment is not in the active workspace.') from error
    if assessment.status != Assessment.Status.DRAFT or (
        assessment.scope_frozen_at and assessment.execution_config
    ):
        raise ValidationError('Only an unstarted draft can be queued. Create a new assessment to run again.')
    requirements, preparation_keys, embedding_model, embedding_dimensions = _ready_approved_scope(assessment)
    selected_documents = list(assessment.documents.select_related('sop_version').all())
    for item in selected_documents:
        selected_key = preparation_keys[str(item.sop_version_id)]
        if item.preparation_key != selected_key:
            item.preparation_key = selected_key
            item.save(update_fields=('preparation_key',))
    selected_versions = [item.sop_version for item in selected_documents]
    assessment.execution_config = {
        'provider': 'openai_compatible',
        'model': os.environ.get('ASSESSMENT_LLM_MODEL', 'gpt-4.1-mini'),
        'endpoint': os.environ.get('ASSESSMENT_LLM_ENDPOINT', 'https://api.openai.com/v1/chat/completions'),
        'prompt_version': FINDING_PROMPT_VERSION,
        'retrieval': {
            'top_k': 10,
            'neighbor_chunks': 0,
            'embedding_models': [embedding_model],
            'embedding_dimensions': [embedding_dimensions],
            'preparation_keys': {str(item.sop_version_id): item.preparation_key for item in selected_documents},
            'broader_review_max_chars': 60_000,
        },
        'provider_validation_retries': 1,
    }
    for requirement in requirements:
        execution, created = RequirementExecution.objects.get_or_create(
            assessment=assessment, requirement=requirement,
        )
        if not created and RequirementExclusion.objects.filter(
            assessment=assessment, requirement=requirement,
        ).exists() and execution.status != RequirementExecution.Status.EXCLUDED:
            execution.status = RequirementExecution.Status.EXCLUDED
            execution.save(update_fields=('status', 'updated_at'))
    AssessmentRunJob.objects.create(assessment=assessment)
    assessment.scope_frozen_at = assessment.scope_frozen_at or timezone.now()
    assessment.status = Assessment.Status.QUEUED
    assessment.save(update_fields=('scope_frozen_at', 'status', 'execution_config', 'updated_at'))
    return assessment


@transaction.atomic
def retry_assessment(*, user, workspace, assessment_id):
    _member(user, workspace)
    try:
        assessment = Assessment.objects.select_for_update().get(pk=assessment_id, workspace=workspace)
        job = AssessmentRunJob.objects.select_for_update().get(assessment=assessment)
    except (Assessment.DoesNotExist, AssessmentRunJob.DoesNotExist) as error:
        raise PermissionDenied('That failed assessment run is not available in this workspace.') from error
    if assessment.status != Assessment.Status.FAILED or job.status != AssessmentRunJob.Status.FAILED:
        raise ValidationError('Only a failed run can be retried.')
    job.status = AssessmentRunJob.Status.QUEUED
    job.attempts = 0
    job.next_attempt_at = timezone.now()
    job.lease_token = None
    job.lease_expires_at = None
    job.last_error = ''
    job.completed_at = None
    job.save(update_fields=(
        'status', 'attempts', 'next_attempt_at', 'lease_token', 'lease_expires_at',
        'last_error', 'completed_at', 'updated_at',
    ))
    RequirementExecution.objects.filter(
        assessment=assessment, status=RequirementExecution.Status.FAILED,
    ).update(status=RequirementExecution.Status.PENDING, error_summary='')
    assessment.status = Assessment.Status.QUEUED
    assessment.save(update_fields=('status', 'updated_at'))
    return assessment


@transaction.atomic
def exclude_requirement(*, user, workspace, assessment_id, requirement_id, reason):
    _member(user, workspace)
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Explain why this requirement does not apply to the assessment scope.')
    if len(reason) > 4000:
        raise ValidationError('Exclusion reason must be 4,000 characters or fewer.')
    try:
        assessment = Assessment.objects.select_for_update().get(pk=assessment_id, workspace=workspace)
    except Assessment.DoesNotExist as error:
        raise PermissionDenied('That assessment is not in the active workspace.') from error
    requirement = Requirement.objects.filter(
        pk=requirement_id, pack_version_id=assessment.pack_version_id,
    ).first()
    if requirement is None:
        raise PermissionDenied('That requirement is not in this assessment.')
    if assessment.status not in (
        Assessment.Status.DRAFT, Assessment.Status.QUEUED,
        Assessment.Status.RUNNING, Assessment.Status.FAILED,
    ):
        raise ValidationError('Applicability exclusions can be recorded only before or during a run.')
    execution, _created = RequirementExecution.objects.select_for_update().get_or_create(
        assessment=assessment, requirement=requirement,
    )
    if Finding.objects.filter(assessment=assessment, requirement=requirement).exists():
        raise ValidationError('A requirement with a saved finding cannot also be excluded.')
    if RequirementExclusion.objects.filter(assessment=assessment, requirement=requirement).exists():
        raise ValidationError('This requirement already has an append-only applicability exclusion.')
    exclusion = RequirementExclusion.objects.create(
        assessment=assessment, requirement=requirement, reviewer=user, reason=reason,
    )
    execution.status = RequirementExecution.Status.EXCLUDED
    execution.error_summary = ''
    execution.save(update_fields=('status', 'error_summary', 'updated_at'))
    return exclusion


@transaction.atomic
def claim_next_job(*, lease_seconds=DEFAULT_LEASE_SECONDS):
    now = timezone.now()
    eligible = (
        Q(status=AssessmentRunJob.Status.QUEUED, next_attempt_at__lte=now)
        | Q(status=AssessmentRunJob.Status.RUNNING) & (
            Q(lease_expires_at__lte=now) | Q(lease_expires_at__isnull=True)
        )
    )
    locking = AssessmentRunJob.objects.select_related('assessment').filter(eligible).order_by('next_attempt_at', 'pk')
    if connection.vendor == 'postgresql':
        locking = locking.select_for_update(skip_locked=True)
    else:
        locking = locking.select_for_update()
    job = locking.first()
    if job is None:
        return None
    if job.attempts >= job.max_attempts:
        job.status = AssessmentRunJob.Status.FAILED
        job.last_error = job.last_error or 'Automatic retry limit was reached.'
        job.lease_token = None
        job.lease_expires_at = None
        job.save(update_fields=('status', 'last_error', 'lease_token', 'lease_expires_at', 'updated_at'))
        assessment = job.assessment
        assessment.status = Assessment.Status.FAILED
        assessment.save(update_fields=('status', 'updated_at'))
        return None
    was_recovered = job.status == AssessmentRunJob.Status.RUNNING
    token = uuid.uuid4()
    claim_count = AssessmentRunJob.objects.filter(pk=job.pk).filter(eligible).filter(
        attempts__lt=job.max_attempts,
    ).update(
        status=AssessmentRunJob.Status.RUNNING,
        attempts=F('attempts') + 1,
        lease_token=token,
        lease_expires_at=now + timedelta(seconds=max(15, int(lease_seconds))),
        started_at=job.started_at or now,
        last_error='',
        updated_at=now,
    )
    if claim_count != 1:
        return None
    job.refresh_from_db()
    assessment = job.assessment
    assessment.status = Assessment.Status.RUNNING
    assessment.save(update_fields=('status', 'updated_at'))
    if was_recovered:
        RequirementExecution.objects.filter(
            assessment=assessment, status=RequirementExecution.Status.RUNNING,
        ).update(status=RequirementExecution.Status.PENDING)
    RequirementExecution.objects.filter(
        assessment=assessment, status=RequirementExecution.Status.FAILED,
    ).update(status=RequirementExecution.Status.PENDING, error_summary='')
    return job


@transaction.atomic
def _renew_lease(job_id, token, *, lease_seconds):
    job = AssessmentRunJob.objects.select_for_update().filter(
        pk=job_id, status=AssessmentRunJob.Status.RUNNING, lease_token=token,
    ).first()
    now = timezone.now()
    if job is None or job.lease_expires_at is None or job.lease_expires_at <= now:
        raise StaleWorkerError('Execution lease expired or was claimed by another worker.')
    job.lease_expires_at = now + timedelta(seconds=max(15, int(lease_seconds)))
    job.save(update_fields=('lease_expires_at', 'updated_at'))
    return job


@transaction.atomic
def _finish_attempt(job_id, token, *, lease_seconds):
    job = AssessmentRunJob.objects.select_for_update().select_related('assessment').filter(
        pk=job_id, status=AssessmentRunJob.Status.RUNNING, lease_token=token,
    ).first()
    now = timezone.now()
    if job is None or job.lease_expires_at is None or job.lease_expires_at <= now:
        raise StaleWorkerError('Execution lease expired before job completion.')
    assessment = job.assessment
    requirements = list(assessment.pack_version.requirements.all())
    missing = [requirement for requirement in requirements if not (
        Finding.objects.filter(assessment=assessment, requirement=requirement).exists()
        or RequirementExclusion.objects.filter(assessment=assessment, requirement=requirement).exists()
    )]
    if not missing:
        job.status = AssessmentRunJob.Status.COMPLETED
        job.completed_at = now
        job.last_error = ''
        assessment.status = Assessment.Status.COMPLETED
    else:
        failure_summaries = list(RequirementExecution.objects.filter(
            assessment=assessment, requirement__in=missing,
        ).exclude(error_summary='').values_list('error_summary', flat=True)[:5])
        job.last_error = '; '.join(failure_summaries) or f'{len(missing)} requirement(s) remain without a finding or exclusion.'
        job.lease_token = None
        job.lease_expires_at = None
        if job.attempts < job.max_attempts:
            delay_seconds = min(300, 5 * (2 ** max(0, job.attempts - 1)))
            job.status = AssessmentRunJob.Status.QUEUED
            job.next_attempt_at = now + timedelta(seconds=delay_seconds)
            assessment.status = Assessment.Status.QUEUED
        else:
            job.status = AssessmentRunJob.Status.FAILED
            assessment.status = Assessment.Status.FAILED
    if job.status == AssessmentRunJob.Status.COMPLETED:
        job.lease_token = None
        job.lease_expires_at = None
    job.save(update_fields=(
        'status', 'completed_at', 'last_error', 'lease_token', 'lease_expires_at',
        'next_attempt_at', 'updated_at',
    ))
    assessment.save(update_fields=('status', 'updated_at'))
    return job


def run_claimed_job(*, job, provider=None, retrieval_provider=None, lease_seconds=DEFAULT_LEASE_SECONDS):
    """Run all pending requirements under the caller's lease, retaining valid partials."""
    if provider is None:
        config = job.assessment.execution_config or {}
        provider = OpenAICompatibleProvider(
            model=config.get('model'), endpoint=config.get('endpoint'),
            timeout=max(5, min(90, int(lease_seconds) - 15)),
            prompt_version=config.get('prompt_version'),
        )
    if retrieval_provider is None:
        retrieval_config = (job.assessment.execution_config or {}).get('retrieval', {})
        embedding_models = retrieval_config.get('embedding_models') or []
        embedding_dimensions = retrieval_config.get('embedding_dimensions') or []
        if embedding_models and embedding_dimensions:
            retrieval_provider = SentenceTransformersProvider(
                model_name=embedding_models[0], dimensions=embedding_dimensions[0],
            )
    token = job.lease_token
    requirements = list(job.assessment.pack_version.requirements.order_by('sort_order', 'code', 'pk'))
    for requirement in requirements:
        if Finding.objects.filter(assessment=job.assessment, requirement=requirement).exists():
            continue
        if RequirementExclusion.objects.filter(assessment=job.assessment, requirement=requirement).exists():
            RequirementExecution.objects.filter(
                assessment=job.assessment, requirement=requirement,
            ).update(status=RequirementExecution.Status.EXCLUDED, error_summary='')
            continue
        try:
            _renew_lease(job.pk, token, lease_seconds=lease_seconds)
            assess_requirement(
                assessment=job.assessment,
                requirement=requirement,
                provider=provider,
                retrieval_provider=retrieval_provider,
                job=job,
                lease_token=token,
            )
        except RequirementExcluded:
            continue
        except StaleWorkerError:
            return 'stale'
        except FindingAssessmentError:
            # The failure was recorded by the one-requirement service. Continue so
            # other checks can still produce durable partial findings.
            continue
        except Exception as error:
            # Unexpected worker errors are recorded as technical execution failures.
            try:
                with transaction.atomic():
                    _renew_lease(job.pk, token, lease_seconds=lease_seconds)
                    execution, _created = RequirementExecution.objects.select_for_update().get_or_create(
                        assessment=job.assessment, requirement=requirement,
                    )
                    if not RequirementExclusion.objects.filter(assessment=job.assessment, requirement=requirement).exists():
                        execution.status = RequirementExecution.Status.FAILED
                        execution.error_summary = f'{type(error).__name__}: {error}'[:4000]
                        execution.save(update_fields=('status', 'error_summary', 'updated_at'))
                        RequirementExecutionFailure.objects.create(
                            assessment=job.assessment, requirement=requirement,
                            attempt_number=max(execution.attempt_count, 1),
                            error_type=type(error).__name__, message=str(error)[:4000],
                        )
            except StaleWorkerError:
                return 'stale'
    try:
        finished = _finish_attempt(job.pk, token, lease_seconds=lease_seconds)
    except StaleWorkerError:
        return 'stale'
    return finished.status


def worker_once(*, provider=None, retrieval_provider=None, lease_seconds=DEFAULT_LEASE_SECONDS):
    job = claim_next_job(lease_seconds=lease_seconds)
    if job is None:
        return None
    return run_claimed_job(
        job=job, provider=provider, retrieval_provider=retrieval_provider,
        lease_seconds=lease_seconds,
    )


def worker_loop(*, provider=None, retrieval_provider=None, poll_interval=2, lease_seconds=DEFAULT_LEASE_SECONDS, once=False):
    outcomes = []
    while True:
        outcome = worker_once(
            provider=provider, retrieval_provider=retrieval_provider, lease_seconds=lease_seconds,
        )
        if outcome is not None:
            outcomes.append(outcome)
        if once:
            return outcomes
        if outcome is None:
            time.sleep(max(0.25, poll_interval))
