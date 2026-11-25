from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render

from apps.documents.models import SOPVersion
from apps.regulations.services import list_published_packs
from apps.workspaces.services import active_membership

from .models import Assessment, AssessmentDocument
from .services import get_assessment, list_assessments, save_assessment


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
    if assessment.status != Assessment.Status.DRAFT or assessment.scope_frozen_at:
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
            messages.success(request, 'Draft assessment saved. Execution is not available yet.')
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
    return render(request, 'assessments/detail.html', {
        'user': request.user, 'workspace': membership.workspace, 'membership': membership,
        'assessment': assessment,
        'documents': assessment.documents.select_related('sop_version__sop').order_by('role', 'pk'),
        'primary_document': assessment.documents.filter(role=AssessmentDocument.Role.PRIMARY).select_related(
            'sop_version__sop',
        ).first(),
    })
