from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction

from .models import Membership, Workspace


@transaction.atomic
def ensure_personal_workspace(user):
    """Give a legacy account its own workspace, safely and idempotently."""
    locked_user = get_user_model().objects.select_for_update().get(pk=user.pk)
    membership = Membership.objects.filter(user=locked_user).select_related('workspace').order_by('pk').first()
    if membership:
        return membership

    profile = getattr(locked_user, 'profile', None)
    company_name = (getattr(profile, 'company_name', '') or '').strip()
    if not company_name:
        company_name = f"{locked_user.get_full_name().strip() or locked_user.username}'s Workspace"

    workspace = Workspace.objects.create(company_name=company_name[:150])
    return Membership.objects.create(
        user=locked_user,
        workspace=workspace,
        role=Membership.Role.OWNER,
    )


def active_membership(request):
    """Resolve the user's active workspace only through their membership."""
    memberships = Membership.objects.filter(user=request.user).select_related('workspace')
    workspace_id = request.session.get('active_workspace_id')
    if workspace_id:
        membership = memberships.filter(workspace_id=workspace_id).first()
        if membership:
            return membership
        request.session.pop('active_workspace_id', None)

    membership = memberships.order_by('pk').first()
    if membership is None:
        membership = ensure_personal_workspace(request.user)
    request.session['active_workspace_id'] = membership.workspace_id
    return membership


def require_owner(membership):
    if membership.role != Membership.Role.OWNER:
        raise PermissionDenied('Workspace owner access is required.')


@transaction.atomic
def remove_membership(membership_id, workspace_id):
    """Remove a member without allowing a workspace to lose its last owner."""
    workspace = Workspace.objects.select_for_update().get(pk=workspace_id)
    membership = Membership.objects.select_for_update().get(pk=membership_id, workspace=workspace)
    if membership.role == Membership.Role.OWNER:
        owner_count = Membership.objects.filter(workspace=workspace, role=Membership.Role.OWNER).count()
        if owner_count <= 1:
            raise ValueError('A workspace must keep at least one owner.')
    membership.delete()


@transaction.atomic
def change_membership_role(membership_id, workspace_id, role):
    workspace = Workspace.objects.select_for_update().get(pk=workspace_id)
    membership = Membership.objects.select_for_update().get(pk=membership_id, workspace=workspace)
    if role not in Membership.Role.values:
        raise ValueError('Choose a valid workspace role.')
    if membership.role == Membership.Role.OWNER and role != Membership.Role.OWNER:
        owner_count = Membership.objects.filter(workspace=workspace, role=Membership.Role.OWNER).count()
        if owner_count <= 1:
            raise ValueError('A workspace must keep at least one owner.')
    membership.role = role
    membership.save(update_fields=['role'])
    return membership
