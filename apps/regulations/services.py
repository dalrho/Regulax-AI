import hashlib
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.workspaces.models import Membership

from .models import RequirementPack, RequirementPackVersion, Requirement, WorkspacePack


def _require_staff(user):
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_staff', False):
        raise PermissionDenied('Staff access is required to administer regulatory packs.')


def _require_membership(user, workspace, *, owner=False):
    if not getattr(user, 'is_authenticated', False):
        raise PermissionDenied('Workspace membership is required.')
    membership = Membership.objects.filter(user=user, workspace=workspace).first()
    if membership is None:
        raise PermissionDenied('Workspace membership is required.')
    if owner and membership.role != Membership.Role.OWNER:
        raise PermissionDenied('Only a workspace owner can manage its regulatory pack selection.')


def _clean_pack_fields(*, slug, title, jurisdiction, authority='', product_category='', description='', scope=''):
    values = {
        'slug': (slug or '').strip().lower(),
        'title': (title or '').strip(),
        'jurisdiction': (jurisdiction or '').strip(),
        'authority': (authority or '').strip(),
        'product_category': (product_category or '').strip(),
        'description': (description or '').strip(),
        'scope': (scope or '').strip(),
    }
    if not values['slug'] or not values['title'] or not values['jurisdiction']:
        raise ValidationError('Pack slug, title, and jurisdiction are required.')
    if len(values['title']) > 200 or len(values['jurisdiction']) > 150:
        raise ValidationError('Pack title or jurisdiction exceeds its maximum length.')
    if len(values['authority']) > 200 or len(values['product_category']) > 150:
        raise ValidationError('Pack authority or product category exceeds its maximum length.')
    return values


@transaction.atomic
def create_pack(*, user, slug, title, jurisdiction, authority='', product_category='', description='', scope=''):
    """Create a catalog entry. Pack catalog administration is staff-only."""
    _require_staff(user)
    pack = RequirementPack(
        **_clean_pack_fields(
            slug=slug, title=title, jurisdiction=jurisdiction, authority=authority,
            product_category=product_category, description=description, scope=scope,
        ),
        created_by=user,
    )
    pack.full_clean()
    pack.save()
    return pack


@transaction.atomic
def create_development_fixture_pack(*, user):
    """Seed the labeled, unapproved initial pack fixture for development use."""
    _require_staff(user)
    from .development_fixtures import (
        DEVELOPMENT_PACK,
        DEVELOPMENT_REQUIREMENTS,
        SOURCE_URL_211_100,
        SOURCE_URL_211_192,
    )

    if RequirementPack.objects.filter(slug=DEVELOPMENT_PACK['slug']).exists():
        raise ValidationError('The development fixture pack already exists.')
    pack = create_pack(user=user, **{
        key: DEVELOPMENT_PACK[key]
        for key in ('slug', 'title', 'authority', 'jurisdiction', 'product_category', 'description', 'scope')
    })
    version = create_pack_version(
        user=user,
        pack_id=pack.pk,
        source_name=DEVELOPMENT_PACK['source_name'],
        source_snapshot_date=DEVELOPMENT_PACK['source_snapshot_date'],
        is_development_fixture=True,
    )
    # Record the appropriate official source URL on every check.
    requirements = []
    for item in DEVELOPMENT_REQUIREMENTS:
        requirements.append({
            **item,
            'source_url': SOURCE_URL_211_100 if '211.100' in item['citation'] else SOURCE_URL_211_192,
        })
    replace_draft_requirements(user=user, version_id=version.pk, requirements=requirements)
    version.source_name = f"{DEVELOPMENT_PACK['source_name']} — 211.100 and 211.192"
    version.save(update_fields=('source_name',))
    return pack, version


@transaction.atomic
def update_pack(*, user, pack_id, **fields):
    _require_staff(user)
    try:
        pack = RequirementPack.objects.select_for_update().get(pk=pack_id)
    except RequirementPack.DoesNotExist as error:
        raise ValidationError('Regulatory pack does not exist.') from error
    allowed = {'slug', 'title', 'jurisdiction', 'authority', 'product_category', 'description', 'scope'}
    if set(fields) - allowed:
        raise ValidationError('Unsupported pack field.')
    values = {
        'slug': fields.get('slug', pack.slug),
        'title': fields.get('title', pack.title),
        'jurisdiction': fields.get('jurisdiction', pack.jurisdiction),
        'authority': fields.get('authority', pack.authority),
        'product_category': fields.get('product_category', pack.product_category),
        'description': fields.get('description', pack.description),
        'scope': fields.get('scope', pack.scope),
    }
    for field, value in _clean_pack_fields(**values).items():
        setattr(pack, field, value)
    pack.full_clean()
    pack.save()
    return pack


@transaction.atomic
def archive_pack(*, user, pack_id):
    _require_staff(user)
    try:
        pack = RequirementPack.objects.select_for_update().get(pk=pack_id)
    except RequirementPack.DoesNotExist as error:
        raise ValidationError('Regulatory pack does not exist.') from error
    if not pack.is_archived:
        pack.is_archived = True
        pack.save(update_fields=('is_archived', 'updated_at'))
    return pack


@transaction.atomic
def create_pack_version(*, user, pack_id, source_url='', source_name='', source_published_on=None,
                         source_snapshot_date=None, effective_from=None, effective_to=None,
                         is_development_fixture=False):
    """Start a new draft revision; requirements must be populated before publish."""
    _require_staff(user)
    try:
        pack = RequirementPack.objects.select_for_update().get(pk=pack_id, is_archived=False)
    except RequirementPack.DoesNotExist as error:
        raise ValidationError('Active regulatory pack does not exist.') from error
    number = (pack.versions.aggregate(number=Max('version_number'))['number'] or 0) + 1
    version = RequirementPackVersion(
        pack=pack,
        version_number=number,
        version_label=f'v{number}',
        source_url=(source_url or '').strip(),
        source_name=(source_name or '').strip(),
        source_snapshot_date=source_snapshot_date,
        source_published_on=source_published_on,
        effective_from=effective_from,
        effective_to=effective_to,
        is_development_fixture=is_development_fixture,
        created_by=user,
    )
    version.full_clean()
    version.save()
    return version


@transaction.atomic
def update_draft_pack_version(*, user, version_id, **fields):
    """Edit source and effective-date metadata before review/publication."""
    _require_staff(user)
    allowed = {
        'source_url', 'source_name', 'source_snapshot_date', 'source_published_on',
        'effective_from', 'effective_to',
    }
    if set(fields) - allowed:
        raise ValidationError('Unsupported pack version field.')
    try:
        version = RequirementPackVersion.objects.select_for_update().get(pk=version_id)
    except RequirementPackVersion.DoesNotExist as error:
        raise ValidationError('Regulatory pack version does not exist.') from error
    if version.status != RequirementPackVersion.Status.DRAFT:
        raise ValidationError('Only draft versions can be edited.')
    for field, value in fields.items():
        setattr(version, field, value.strip() if isinstance(value, str) else value)
    version.reviewed_by = None
    version.reviewed_at = None
    version.review_notes = ''
    version.full_clean()
    version.save()
    return version


def _normalize_requirement(item, index):
    if not isinstance(item, dict):
        raise ValidationError(f'Requirement {index + 1} must be an object.')
    allowed = {
        'code', 'title', 'citation', 'source_url', 'original_text', 'assessment_question', 'category',
        'applicability_notes', 'assessment_limits', 'is_mandatory', 'sort_order',
    }
    if set(item) - allowed:
        raise ValidationError(f'Requirement {index + 1} contains unsupported fields.')
    values = {key: item.get(key, '') for key in (
        'code', 'title', 'citation', 'source_url', 'original_text', 'assessment_question', 'category',
        'applicability_notes', 'assessment_limits',
    )}
    for key, value in values.items():
        if not isinstance(value, str):
            raise ValidationError(f'Requirement {index + 1} field {key} must be text.')
        values[key] = value.strip()
    if values['source_url']:
        URLValidator()(values['source_url'])
    if not all(values[key] for key in ('code', 'title', 'citation', 'original_text', 'assessment_question')):
        raise ValidationError(f'Requirement {index + 1} needs code, title, citation, source text, and an assessment question.')
    if len(values['code']) > 100 or len(values['title']) > 250 or len(values['category']) > 150 or len(values['citation']) > 250:
        raise ValidationError(f'Requirement {index + 1} has a field over its maximum length.')
    mandatory = item.get('is_mandatory', True)
    if not isinstance(mandatory, bool):
        raise ValidationError(f'Requirement {index + 1} is_mandatory must be true or false.')
    sort_order = item.get('sort_order', index)
    if not isinstance(sort_order, int) or isinstance(sort_order, bool) or sort_order < 0:
        raise ValidationError(f'Requirement {index + 1} sort_order must be a non-negative integer.')
    return {
        **values,
        'is_mandatory': mandatory,
        'sort_order': sort_order,
    }


@transaction.atomic
def replace_draft_requirements(*, user, version_id, requirements):
    """Replace all clauses in a draft revision with validated structured data."""
    _require_staff(user)
    if not isinstance(requirements, list):
        raise ValidationError('Requirements must be supplied as a list.')
    if len(requirements) > 5000:
        raise ValidationError('A pack version cannot contain more than 5,000 requirements.')
    normalized = [_normalize_requirement(item, index) for index, item in enumerate(requirements)]
    codes = [item['code'] for item in normalized]
    if len(codes) != len(set(codes)):
        raise ValidationError('Requirement codes must be unique within a pack version.')
    try:
        version = RequirementPackVersion.objects.select_for_update().get(pk=version_id)
    except RequirementPackVersion.DoesNotExist as error:
        raise ValidationError('Regulatory pack version does not exist.') from error
    if version.status != RequirementPackVersion.Status.DRAFT:
        raise ValidationError('Only draft versions can be edited.')
    version.requirements.all().delete()
    Requirement.objects.bulk_create([
        Requirement(pack_version=version, **item) for item in normalized
    ])
    version.reviewed_by = None
    version.reviewed_at = None
    version.review_notes = ''
    version.save(update_fields=('reviewed_by', 'reviewed_at', 'review_notes'))
    return version


def _snapshot_hash(version, requirements):
    snapshot = {
        'pack': {
            'slug': version.pack.slug,
            'title': version.pack.title,
            'authority': version.pack.authority,
            'jurisdiction': version.pack.jurisdiction,
            'product_category': version.pack.product_category,
        },
        'version': {
            'label': version.version_label,
            'source_url': version.source_url,
            'source_name': version.source_name,
            'source_snapshot_date': str(version.source_snapshot_date or ''),
            'source_published_on': str(version.source_published_on or ''),
            'effective_from': str(version.effective_from or ''),
            'effective_to': str(version.effective_to or ''),
            'scope': version.pack.scope,
            'description': version.pack.description,
        },
        'requirements': [
            {
                'code': row.code,
                'title': row.title,
                'citation': row.citation,
                'source_url': row.source_url,
                'original_text': row.original_text,
                'assessment_question': row.assessment_question,
                'category': row.category,
                'applicability_notes': row.applicability_notes,
                'assessment_limits': row.assessment_limits,
                'is_mandatory': row.is_mandatory,
                'sort_order': row.sort_order,
            }
            for row in requirements
        ],
    }
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


@transaction.atomic
def publish_pack_version(*, user, version_id):
    """Freeze a complete draft and make it available for workspace selection."""
    _require_staff(user)
    try:
        version = RequirementPackVersion.objects.select_for_update().select_related('pack').get(pk=version_id)
    except RequirementPackVersion.DoesNotExist as error:
        raise ValidationError('Regulatory pack version does not exist.') from error
    if version.status != RequirementPackVersion.Status.DRAFT:
        raise ValidationError('Only draft versions can be published.')
    if version.is_development_fixture:
        raise ValidationError('Development fixtures cannot be published as approved regulatory packs.')
    if version.reviewed_by_id is None or version.reviewed_at is None:
        raise ValidationError('A pack version must be reviewed before publication.')
    requirements = list(version.requirements.order_by('sort_order', 'code', 'pk'))
    if not requirements:
        raise ValidationError('Add at least one requirement before publishing a pack version.')
    version.content_hash = _snapshot_hash(version, requirements)
    version.status = RequirementPackVersion.Status.PUBLISHED
    version.published_by = user
    version.published_at = timezone.now()
    version.save()
    return version


@transaction.atomic
def record_pack_review(*, user, version_id, review_notes):
    """Record that an authorized staff member reviewed this draft's sources and checks."""
    _require_staff(user)
    try:
        version = RequirementPackVersion.objects.select_for_update().get(pk=version_id)
    except RequirementPackVersion.DoesNotExist as error:
        raise ValidationError('Regulatory pack version does not exist.') from error
    if version.status != RequirementPackVersion.Status.DRAFT:
        raise ValidationError('Only draft versions can be reviewed.')
    review_notes = (review_notes or '').strip()
    if not review_notes:
        raise ValidationError('Record review notes before approving publication.')
    version.reviewed_by = user
    version.reviewed_at = timezone.now()
    version.review_notes = review_notes
    version.save()
    return version


def list_published_packs(*, user, jurisdiction=None, product_category=None, search=''):
    """List published catalog revisions visible for workspace pack selection."""
    if not getattr(user, 'is_authenticated', False):
        raise PermissionDenied('Sign-in is required to access regulatory packs.')
    queryset = RequirementPackVersion.objects.filter(
        status=RequirementPackVersion.Status.PUBLISHED,
        pack__is_archived=False,
        is_development_fixture=False,
    ).select_related('pack').prefetch_related('requirements')
    if jurisdiction:
        queryset = queryset.filter(pack__jurisdiction__iexact=jurisdiction.strip())
    if product_category:
        queryset = queryset.filter(pack__product_category__iexact=product_category.strip())
    search = (search or '').strip()
    if search:
        queryset = queryset.filter(pack__title__icontains=search)
    return queryset.order_by('pack__jurisdiction', 'pack__title', '-version_number')


@transaction.atomic
def select_workspace_pack(*, user, workspace, version_id):
    """Pin a published version to a workspace; later revisions do not move it."""
    _require_membership(user, workspace, owner=True)
    try:
        version = RequirementPackVersion.objects.select_related('pack').get(
            pk=version_id,
            status=RequirementPackVersion.Status.PUBLISHED,
            pack__is_archived=False,
            is_development_fixture=False,
        )
    except RequirementPackVersion.DoesNotExist as error:
        raise ValidationError('Only a published revision of an active pack can be selected.') from error
    assignment, _ = WorkspacePack.objects.get_or_create(
        workspace=workspace,
        pack_version=version,
        defaults={'selected_by': user},
    )
    return assignment


def list_workspace_packs(*, user, workspace):
    _require_membership(user, workspace)
    return WorkspacePack.objects.filter(workspace=workspace).select_related(
        'pack_version__pack',
    ).prefetch_related('pack_version__requirements').order_by('pack_version__pack__title')


@transaction.atomic
def remove_workspace_pack(*, user, workspace, assignment_id):
    _require_membership(user, workspace, owner=True)
    try:
        assignment = WorkspacePack.objects.select_for_update().get(pk=assignment_id, workspace=workspace)
    except WorkspacePack.DoesNotExist as error:
        raise PermissionDenied('That selected regulatory pack is not in the active workspace.') from error
    assignment.delete()
