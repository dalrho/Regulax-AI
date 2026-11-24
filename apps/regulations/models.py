from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class RequirementPack(models.Model):
    """A regulation collection identified independently of its revisions."""

    slug = models.SlugField(max_length=120, unique=True)
    title = models.CharField(max_length=200)
    authority = models.CharField(max_length=200, blank=True)
    jurisdiction = models.CharField(max_length=150)
    product_category = models.CharField(max_length=150, blank=True)
    description = models.TextField(blank=True)
    scope = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_regulatory_packs',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('jurisdiction', 'title', 'pk')

    def __str__(self):
        return f'{self.title} ({self.jurisdiction})'

    def save(self, *args, **kwargs):
        if self.pk and self.versions.filter(status=RequirementPackVersion.Status.PUBLISHED).exists():
            original = type(self).objects.get(pk=self.pk)
            frozen = ('slug', 'title', 'authority', 'jurisdiction', 'product_category', 'description', 'scope')
            if any(getattr(original, field) != getattr(self, field) for field in frozen):
                raise ValidationError('Pack scope and identity are frozen after publication; create a new pack.')
        super().save(*args, **kwargs)

    @property
    def latest_published_version(self):
        return self.versions.filter(status=RequirementPackVersion.Status.PUBLISHED).order_by('-version_number').first()


class RequirementPackVersionQuerySet(models.QuerySet):
    def _reject_published(self):
        if self.filter(status=RequirementPackVersion.Status.PUBLISHED).exists():
            raise ValidationError('Published regulatory versions are immutable; create a new version.')

    def update(self, **kwargs):
        self._reject_published()
        if 'status' in kwargs:
            raise ValidationError('Use the pack publication service to change version status.')
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        self._reject_published()
        if 'status' in fields:
            raise ValidationError('Use the pack publication service to change version status.')
        return super().bulk_update(objs, fields, **kwargs)

    def delete(self):
        raise ValidationError('Regulatory pack versions cannot be deleted.')


class RequirementPackVersion(models.Model):
    """A versioned regulatory snapshot; published versions are read-only."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'

    pack = models.ForeignKey(RequirementPack, on_delete=models.PROTECT, related_name='versions')
    version_number = models.PositiveIntegerField()
    version_label = models.CharField(max_length=30)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    source_url = models.URLField(blank=True)
    source_name = models.CharField(max_length=200, blank=True)
    source_snapshot_date = models.DateField(null=True, blank=True)
    source_published_on = models.DateField(null=True, blank=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, editable=False, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_regulatory_pack_versions',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='reviewed_regulatory_pack_versions',
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    is_development_fixture = models.BooleanField(default=False)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='published_regulatory_pack_versions',
        null=True,
        blank=True,
    )
    published_at = models.DateTimeField(null=True, blank=True)

    objects = RequirementPackVersionQuerySet.as_manager()

    class Meta:
        ordering = ('pack_id', 'version_number')
        constraints = [
            models.UniqueConstraint(fields=('pack', 'version_number'), name='unique_pack_version_number'),
            models.UniqueConstraint(fields=('pack', 'version_label'), name='unique_pack_version_label'),
        ]

    def __str__(self):
        return f'{self.pack.title} — {self.version_label}'

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values().first()
            if original:
                changed = any(
                    original[field.attname] != getattr(self, field.attname)
                    for field in self._meta.concrete_fields if not field.primary_key
                )
                if original['status'] != self.Status.DRAFT and changed:
                    raise ValidationError('Published regulatory versions are immutable; create a new version.')
                if original['status'] == self.Status.DRAFT and self.status == self.Status.PUBLISHED:
                    if (
                        self.is_development_fixture
                        or self.reviewed_by_id is None
                        or self.reviewed_at is None
                        or self.published_by_id is None
                        or self.published_at is None
                        or len(self.content_hash) != 64
                        or not self.requirements.exists()
                    ):
                        raise ValidationError('A reviewed, complete, non-fixture pack version is required for publication.')
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'End date must be on or after the start date.'})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Regulatory pack versions cannot be deleted.')


class RequirementQuerySet(models.QuerySet):
    def _reject_published(self):
        if self.filter(pack_version__status__in=(
            RequirementPackVersion.Status.PUBLISHED,
        )).exists():
            raise ValidationError('Requirements in published regulatory versions are immutable.')

    def update(self, **kwargs):
        self._reject_published()
        target_version_id = kwargs.get('pack_version_id')
        if target_version_id is None and 'pack_version' in kwargs:
            target_version_id = getattr(kwargs['pack_version'], 'pk', kwargs['pack_version'])
        if target_version_id:
            from django.db.models import Q
            if RequirementPackVersion.objects.filter(
                Q(pk=target_version_id), status=RequirementPackVersion.Status.PUBLISHED,
            ).exists():
                raise ValidationError('Requirements can only be moved to draft versions.')
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        self._reject_published()
        if any(obj.pack_version.status != RequirementPackVersion.Status.DRAFT for obj in objs):
            raise ValidationError('Requirements in published regulatory versions are immutable.')
        return super().bulk_update(objs, fields, **kwargs)

    def bulk_create(self, objs, **kwargs):
        if any(obj.pack_version.status != RequirementPackVersion.Status.DRAFT for obj in objs):
            raise ValidationError('Requirements can only be added to draft regulatory versions.')
        return super().bulk_create(objs, **kwargs)

    def delete(self):
        self._reject_published()
        return super().delete()


class Requirement(models.Model):
    """One clause/check within a pack version."""

    pack_version = models.ForeignKey(RequirementPackVersion, on_delete=models.PROTECT, related_name='requirements')
    code = models.CharField(max_length=100)
    title = models.CharField(max_length=250)
    citation = models.CharField(max_length=250)
    source_url = models.URLField(blank=True)
    original_text = models.TextField()
    assessment_question = models.TextField()
    category = models.CharField(max_length=150, blank=True)
    applicability_notes = models.TextField(blank=True)
    assessment_limits = models.TextField(blank=True)
    is_mandatory = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('sort_order', 'code', 'pk')
        constraints = [
            models.UniqueConstraint(fields=('pack_version', 'code'), name='unique_requirement_code_per_pack_version'),
        ]
        indexes = [models.Index(fields=('pack_version', 'sort_order'))]

    objects = RequirementQuerySet.as_manager()

    def __str__(self):
        return f'{self.code}: {self.title}'

    def save(self, *args, **kwargs):
        if self.pack_version_id and self.pack_version.status != RequirementPackVersion.Status.DRAFT:
            raise ValidationError('Requirements in published regulatory versions are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pack_version.status != RequirementPackVersion.Status.DRAFT:
            raise ValidationError('Requirements in published regulatory versions cannot be deleted.')
        super().delete(*args, **kwargs)


class WorkspacePack(models.Model):
    """A workspace's explicit selection of a published pack revision."""

    workspace = models.ForeignKey('workspaces.Workspace', on_delete=models.CASCADE, related_name='regulatory_packs')
    pack_version = models.ForeignKey(RequirementPackVersion, on_delete=models.PROTECT, related_name='workspace_assignments')
    selected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='selected_regulatory_packs',
    )
    selected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('workspace', 'pack_version'), name='unique_workspace_pack_version'),
        ]
        indexes = [models.Index(fields=('workspace', 'selected_at'))]

    def clean(self):
        if self.pack_version_id and (
            self.pack_version.status != RequirementPackVersion.Status.PUBLISHED
            or self.pack_version.is_development_fixture
            or self.pack_version.pack.is_archived
        ):
            raise ValidationError({'pack_version': 'Only published approved versions of active packs can be selected.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.workspace} — {self.pack_version}'
