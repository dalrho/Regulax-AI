from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from apps.workspaces.services import ensure_personal_workspace
from apps.documents.services import list_sops
from apps.assessments.services import list_assessments


@login_required
def dashboard_view(request):
    membership = ensure_personal_workspace(request.user)
    request.session['active_workspace_id'] = membership.workspace_id
    assessments = list_assessments(user=request.user, workspace=membership.workspace)
    return render(request, 'dashboard/dashboard.html', {
        'user': request.user,
        'workspace': membership.workspace,
        'membership': membership,
        'sop_count': list_sops(user=request.user, workspace=membership.workspace).count(),
        'assessment_count': assessments.count(),
        'recent_assessments': assessments[:5],
    })

