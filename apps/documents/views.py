from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.workspaces.services import active_membership

from .models import SOP
from .services import archive_sop, create_sop, get_sop, list_sops


def _sop_context(request, membership, **extra):
    return {
        'user': request.user,
        'workspace': membership.workspace,
        'membership': membership,
        **extra,
    }


@login_required
def sop_library_view(request):
    membership = active_membership(request)
    query = request.GET.get('q', '').strip()
    include_archived = request.GET.get('archived') == '1'
    sops = list_sops(
        user=request.user,
        workspace=membership.workspace,
        include_archived=include_archived,
        search=query,
    )
    errors = []

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        category = request.POST.get('category', '').strip()
        pasted_text = request.POST.get('text', '')
        upload = request.FILES.get('file')
        try:
            sop, _version = create_sop(
                user=request.user,
                workspace=membership.workspace,
                title=title or (upload.name.rsplit('.', 1)[0] if upload else ''),
                category=category,
                text=None if upload else pasted_text,
                uploaded_file=upload,
            )
            messages.success(request, f'“{sop.title}” was added to your SOP library.')
            return redirect('sop_detail', sop_id=sop.pk)
        except ValidationError as error:
            if hasattr(error, 'message_dict'):
                errors = [message for field_errors in error.message_dict.values() for message in field_errors]
            else:
                errors = error.messages

    return render(request, 'documents/sop_library.html', _sop_context(
        request,
        membership,
        sops=sops,
        query=query,
        include_archived=include_archived,
        upload_errors=errors,
        sop_count=list_sops(user=request.user, workspace=membership.workspace).count(),
    ))


@login_required
def sop_detail_view(request, sop_id):
    membership = active_membership(request)
    sop = get_sop(user=request.user, workspace=membership.workspace, sop_id=sop_id)
    versions = sop.versions.order_by('-version_number')
    try:
        highlighted_version_id = int(request.GET.get('version', ''))
    except (TypeError, ValueError):
        highlighted_version_id = None
    if not versions.filter(pk=highlighted_version_id).exists():
        highlighted_version_id = None
    return render(request, 'documents/sop_detail.html', _sop_context(
        request, membership, sop=sop, versions=versions, highlighted_version_id=highlighted_version_id,
    ))


@login_required
@require_POST
def sop_archive_view(request, sop_id):
    membership = active_membership(request)
    sop = archive_sop(user=request.user, workspace=membership.workspace, sop_id=sop_id)
    messages.success(request, f'“{sop.title}” was archived.')
    return redirect('sop_library')
