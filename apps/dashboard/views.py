from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from apps.workspaces.services import ensure_personal_workspace


@login_required
def dashboard_view(request):
    membership = ensure_personal_workspace(request.user)
    request.session['active_workspace_id'] = membership.workspace_id
    return render(request, 'dashboard/dashboard.html', {
        'user': request.user,
        'workspace': membership.workspace,
        'membership': membership,
    })

