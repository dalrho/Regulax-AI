from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.documents.models import SOPVersion
from apps.regulations.services import list_published_packs
from apps.workspaces.services import active_membership

from .execution import exclude_requirement, queue_assessment, retry_assessment
from .models import (
    Assessment,
    AssessmentDocument,
    Finding,
    RequirementExecution,
)
from .services import get_assessment, list_assessments, record_finding_review, save_assessment


def _form_options(user, workspace, assessment=None):
    versions = SOPVersion.objects.filter(sop__workspace=workspace, sop__is_archived=False).select_related(
        'sop', 'sop__workspace',
    ).order_by('sop__title', '-version_number')
    packs = list_published_packs(user=user, jurisdiction=workspace.jurisdiction or None)
    selected = {'supporting_ids': []}
    if assessment:
        for item in assessment.documents.all():
            if item.role == AssessmentDocument.Role.PRIMARY:
                selected['primary'] = item.sop_version_id
            else:
                selected['supporting_ids'].append(item.sop_version_id)
    return versions, packs, selected


@login_required
def assessment_history_view(request):
    membership = active_membership(request)
    assessments = list_assessments(user=request.user, workspace=membership.workspace)
    return render(request, 'assessments/history.html', {
        'user': request.user, 'workspace': membership.workspace, 'membership': membership,
        'assessments': assessments,
    })


@login_required
def assessment_create_view(request):
    return _assessment_form_view(request)


@login_required
def assessment_edit_view(request, assessment_id):
    membership = active_membership(request)
    assessment = get_assessment(user=request.user, workspace=membership.workspace, assessment_id=assessment_id)
    if assessment.status != Assessment.Status.DRAFT or assessment.scope_frozen_at or assessment.requirement_exclusions.exists():
        raise PermissionDenied('Only an unstarted draft can be edited.')
    return _assessment_form_view(request, assessment=assessment)


def _assessment_form_view(request, assessment=None):
    membership = active_membership(request)
    workspace = membership.workspace
    errors = []
    if request.method == 'POST':
        try:
            assessment = save_assessment(
                user=request.user, workspace=workspace,
                name=request.POST.get('name'),
                pack_version_id=request.POST.get('pack_version'),
                primary_version_id=request.POST.get('primary_version'),
                supporting_version_ids=request.POST.getlist('supporting_versions'),
                scope_confirmed=request.POST.get('scope_confirmed') == 'yes',
                assessment_id=assessment.pk if assessment else None,
            )
            messages.success(request, 'Draft assessment saved. Review the selected scope and readiness before queueing it.')
            return redirect('assessment_detail', assessment_id=assessment.pk)
        except ValidationError as error:
            if hasattr(error, 'message_dict'):
                errors = [message for field_errors in error.message_dict.values() for message in field_errors]
            else:
                errors = error.messages
    versions, packs, selected = _form_options(request.user, workspace, assessment)
    return render(request, 'assessments/form.html', {
        'user': request.user, 'workspace': workspace, 'membership': membership,
        'versions': versions, 'packs': packs, 'selected': selected,
        'assessment': assessment, 'errors': errors,
    })


@login_required
def assessment_detail_view(request, assessment_id):
    membership = active_membership(request)
    assessment = get_assessment(user=request.user, workspace=membership.workspace, assessment_id=assessment_id)
    requirements = list(assessment.pack_version.requirements.order_by('sort_order', 'code', 'pk'))
    executions = {item.requirement_id: item for item in assessment.requirement_executions.all()}
    findings = list(assessment.findings.select_related('requirement').prefetch_related('evidence__sop_version__sop', 'reviews__reviewer'))
    for finding in findings:
        evidence_items = list(finding.evidence.all())
        finding.supporting_evidence_count = sum(item.kind == 'supporting' for item in evidence_items)
        finding.limitation_evidence_count = sum(item.kind == 'limitation' for item in evidence_items)
    finding_by_requirement = {item.requirement_id: item for item in findings}
    exclusions = {item.requirement_id: item for item in assessment.requirement_exclusions.select_related('reviewer')}
    requirement_rows = [{
        'requirement': requirement,
        'execution': executions.get(requirement.pk),
        'finding': finding_by_requirement.get(requirement.pk),
        'exclusion': exclusions.get(requirement.pk),
    } for requirement in requirements]
    can_run = False
    run_error = ''
    if assessment.status == Assessment.Status.DRAFT and not assessment.execution_config:
        from .execution import _ready_approved_scope
        try:
            _ready_approved_scope(assessment)
            can_run = True
        except ValidationError as error:
            run_error = ' '.join(error.messages)
    job = getattr(assessment, 'run_job', None)
    return render(request, 'assessments/detail.html', {
        'user': request.user, 'workspace': membership.workspace, 'membership': membership,
        'assessment': assessment,
        'documents': assessment.documents.select_related('sop_version__sop').order_by('role', 'pk'),
        'primary_document': assessment.documents.filter(role=AssessmentDocument.Role.PRIMARY).select_related(
            'sop_version__sop',
        ).first(),
        'requirements': requirements,
        'executions': executions,
        'requirement_rows': requirement_rows,
        'findings': findings,
        'total_requirements': len(requirements),
        'accounted_count': len(finding_by_requirement) + len(exclusions),
        'can_run': can_run,
        'can_edit_draft': (
            assessment.status == Assessment.Status.DRAFT
            and not assessment.scope_frozen_at
            and not assessment.requirement_exclusions.exists()
        ),
        'run_error': run_error,
        'job': job,
    })


@login_required
@require_POST
def assessment_run_view(request, assessment_id):
    membership = active_membership(request)
    try:
        assessment = queue_assessment(
            user=request.user, workspace=membership.workspace, assessment_id=assessment_id,
        )
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, 'Assessment queued. It will run in the background and keep per-requirement progress.')
    return redirect('assessment_detail', assessment_id=assessment_id)


@login_required
@require_POST
def assessment_retry_view(request, assessment_id):
    membership = active_membership(request)
    try:
        retry_assessment(user=request.user, workspace=membership.workspace, assessment_id=assessment_id)
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, 'Assessment retry queued. Existing valid findings were retained.')
    return redirect('assessment_detail', assessment_id=assessment_id)


@login_required
@require_POST
def requirement_exclude_view(request, assessment_id, requirement_id):
    membership = active_membership(request)
    try:
        exclusion = exclude_requirement(
            user=request.user, workspace=membership.workspace, assessment_id=assessment_id,
            requirement_id=requirement_id, reason=request.POST.get('reason', ''),
        )
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, f'{exclusion.requirement.code} was excluded with a recorded reviewer reason.')
    return redirect('assessment_detail', assessment_id=assessment_id)


@login_required
def assessment_progress_view(request, assessment_id):
    membership = active_membership(request)
    assessment = get_assessment(user=request.user, workspace=membership.workspace, assessment_id=assessment_id)
    total = assessment.pack_version.requirements.count()
    completed_findings = assessment.findings.count()
    excluded = assessment.requirement_exclusions.count()
    return JsonResponse({
        'status': assessment.status,
        'total': total,
        'findings': completed_findings,
        'excluded': excluded,
        'accounted': completed_findings + excluded,
        'failures': assessment.requirement_executions.filter(status=RequirementExecution.Status.FAILED).count(),
    })


@login_required
def finding_detail_view(request, assessment_id, finding_id):
    membership = active_membership(request)
    assessment = get_assessment(user=request.user, workspace=membership.workspace, assessment_id=assessment_id)
    finding = get_object_or_404(
        Finding.objects.select_related('requirement').prefetch_related(
            'evidence__chunk', 'evidence__sop_version__sop', 'reviews__reviewer',
        ),
        pk=finding_id, assessment=assessment,
    )
    return render(request, 'assessments/finding_detail.html', {
        'user': request.user, 'workspace': membership.workspace, 'membership': membership,
        'assessment': assessment, 'finding': finding,
    })


@login_required
@require_POST
def finding_review_view(request, assessment_id, finding_id):
    membership = active_membership(request)
    try:
        review = record_finding_review(
            user=request.user, workspace=membership.workspace, finding_id=finding_id,
            decision=request.POST.get('decision'), note=request.POST.get('note', ''),
            assessment_id=assessment_id,
        )
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, f'Reviewer decision “{review.get_decision_display()}” was appended.')
    return redirect('finding_detail', assessment_id=assessment_id, finding_id=finding_id)
