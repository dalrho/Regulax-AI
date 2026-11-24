from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .forms import AddMemberForm, WorkspaceSettingsForm
from .models import Membership, Workspace
from .services import (
    active_membership,
    change_membership_role,
    remove_membership,
    require_owner,
)


def _owner_membership(request):
    membership = active_membership(request)
    require_owner(membership)
    return membership


@login_required
def settings_view(request):
    membership = _owner_membership(request)
    if request.method == 'POST':
        form = WorkspaceSettingsForm(request.POST, instance=membership.workspace)
        if form.is_valid():
            form.save()
            messages.success(request, 'Workspace settings saved.')
            return redirect('workspace_settings')
    else:
        form = WorkspaceSettingsForm(instance=membership.workspace)
    return render(request, 'workspaces/settings.html', {
        'form': form,
        'workspace': membership.workspace,
        'membership': membership,
    })


@login_required
def members_view(request):
    membership = _owner_membership(request)
    if request.method == 'POST':
        form = AddMemberForm(request.POST, workspace=membership.workspace)
        if form.is_valid():
            form.save()
            messages.success(request, 'Workspace member added.')
            return redirect('workspace_members')
    else:
        form = AddMemberForm(workspace=membership.workspace, initial={'role': Membership.Role.REVIEWER})
    members = Membership.objects.filter(workspace=membership.workspace).select_related('user').order_by('created_at', 'pk')
    return render(request, 'workspaces/members.html', {
        'form': form,
        'members': members,
        'membership': membership,
        'workspace': membership.workspace,
        'role_choices': Membership.Role.choices,
    })


@login_required
@require_POST
def member_role_view(request, membership_id):
    owner_membership = _owner_membership(request)
    try:
        change_membership_role(
            membership_id,
            owner_membership.workspace_id,
            request.POST.get('role'),
        )
    except (Workspace.DoesNotExist, Membership.DoesNotExist):
        raise PermissionDenied('That membership is not in your active workspace.')
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'Member role updated.')
    return redirect('workspace_members')


@login_required
@require_POST
def member_remove_view(request, membership_id):
    owner_membership = _owner_membership(request)
    try:
        remove_membership(membership_id, owner_membership.workspace_id)
    except (Workspace.DoesNotExist, Membership.DoesNotExist):
        raise PermissionDenied('That membership is not in your active workspace.')
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'Workspace member removed.')
    return redirect('workspace_members')
