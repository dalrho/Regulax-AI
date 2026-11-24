import hashlib

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Q

from apps.workspaces.models import Membership

from .models import SOP, SOPVersion


DEFAULT_MAX_SOP_BYTES = 1_048_576


def canonicalize_text(text):
    """Normalize transport differences once while preserving document spacing."""
    if not isinstance(text, str):
        raise ValidationError('SOP text must be valid UTF-8 text.')
    return text.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n')


def content_sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _max_sop_bytes():
    return getattr(settings, 'SOP_MAX_UPLOAD_BYTES', DEFAULT_MAX_SOP_BYTES)


def _validated_text(text):
    canonical_text = canonicalize_text(text)
    if not canonical_text.strip():
        raise ValidationError('SOP text cannot be empty.')
    if len(canonical_text.encode('utf-8')) > _max_sop_bytes():
        raise ValidationError(f'SOP text must be no larger than {_max_sop_bytes()} bytes.')
    return canonical_text


def decode_txt_upload(uploaded_file):
    """Read and validate a bounded UTF-8 .txt upload without storing the file."""
    filename = getattr(uploaded_file, 'name', '')
    if not filename.lower().endswith('.txt'):
        raise ValidationError('Upload a UTF-8 .txt file.')

    raw = uploaded_file.read(_max_sop_bytes() + 1)
    if len(raw) > _max_sop_bytes():
        raise ValidationError(f'Uploaded file must be no larger than {_max_sop_bytes()} bytes.')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise ValidationError('The .txt file must use UTF-8 encoding.') from error
    return _validated_text(text)


def _require_workspace_membership(user, workspace):
    if not getattr(user, 'is_authenticated', False) or not Membership.objects.filter(
        user=user,
        workspace=workspace,
    ).exists():
        raise PermissionDenied('Workspace membership is required to access its SOP library.')


def _clean_metadata(title, category):
    title = (title or '').strip()
    category = (category or '').strip()
    if not title:
        raise ValidationError({'title': 'Enter an SOP title.'})
    if len(title) > 200:
        raise ValidationError({'title': 'SOP title must be 200 characters or fewer.'})
    if len(category) > 150:
        raise ValidationError({'category': 'Category must be 150 characters or fewer.'})
    return title, category


def _create_next_version(sop, uploaded_by, canonical_text):
    latest_number = sop.versions.aggregate(number=Max('version_number'))['number'] or 0
    number = latest_number + 1
    return SOPVersion.objects.create(
        sop=sop,
        version_number=number,
        version_label=f'v{number}',
        canonical_text=canonical_text,
        uploaded_by=uploaded_by,
    )


@transaction.atomic
def create_sop(*, user, workspace, title, category='', text=None, uploaded_file=None):
    """Create an SOP and its first version atomically from pasted or uploaded text."""
    _require_workspace_membership(user, workspace)
    title, category = _clean_metadata(title, category)
    if (text is None) == (uploaded_file is None):
        raise ValidationError('Provide either pasted SOP text or one .txt upload.')
    canonical_text = decode_txt_upload(uploaded_file) if uploaded_file is not None else _validated_text(text)

    sop = SOP.objects.create(
        workspace=workspace,
        title=title,
        category=category,
        created_by=user,
    )
    version = _create_next_version(sop, user, canonical_text)
    return sop, version


@transaction.atomic
def create_sop_version(*, user, workspace, sop_id, text=None, uploaded_file=None):
    """Append an immutable version to a workspace SOP."""
    _require_workspace_membership(user, workspace)
    if (text is None) == (uploaded_file is None):
        raise ValidationError('Provide either pasted SOP text or one .txt upload.')
    canonical_text = decode_txt_upload(uploaded_file) if uploaded_file is not None else _validated_text(text)

    try:
        sop = SOP.objects.select_for_update().get(pk=sop_id, workspace=workspace)
    except SOP.DoesNotExist as error:
        raise PermissionDenied('That SOP is not in the active workspace.') from error
    if sop.is_archived:
        raise ValidationError('Archived SOPs cannot receive new versions.')
    return _create_next_version(sop, user, canonical_text)


def list_sops(*, user, workspace, include_archived=False, search=''):
    """Return only SOPs belonging to a workspace the user can access."""
    _require_workspace_membership(user, workspace)
    queryset = SOP.objects.filter(workspace=workspace)
    if not include_archived:
        queryset = queryset.filter(is_archived=False)
    search = (search or '').strip()
    if search:
        normalized_id = search.upper().removeprefix('SOP-')
        id_match = Q(pk=int(normalized_id)) if normalized_id.isdigit() else Q(pk__in=[])
        queryset = queryset.filter(Q(title__icontains=search) | id_match)
    return queryset.prefetch_related('versions').order_by('title', 'pk')


def get_sop(*, user, workspace, sop_id):
    _require_workspace_membership(user, workspace)
    try:
        return SOP.objects.prefetch_related('versions__uploaded_by').get(
            pk=sop_id,
            workspace=workspace,
        )
    except SOP.DoesNotExist as error:
        raise PermissionDenied('That SOP is not in the active workspace.') from error


@transaction.atomic
def archive_sop(*, user, workspace, sop_id):
    _require_workspace_membership(user, workspace)
    try:
        sop = SOP.objects.select_for_update().get(pk=sop_id, workspace=workspace)
    except SOP.DoesNotExist as error:
        raise PermissionDenied('That SOP is not in the active workspace.') from error
    if not sop.is_archived:
        sop.is_archived = True
        sop.save(update_fields=('is_archived', 'updated_at'))
    return sop
