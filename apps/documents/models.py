from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .fields import EmbeddingVectorField


class SOPVersionQuerySet(models.QuerySet):
    IMMUTABLE_FIELDS = {
        'sop', 'sop_id', 'version_number', 'version_label',
        'canonical_text', 'content_hash', 'uploaded_by', 'uploaded_by_id',
        'created_at',
        'processing_status', 'preparation_error', 'embedding_model',
        'embedding_dimensions', 'active_preparation_key', 'prepared_at',
    }

    def update(self, **kwargs):
        if self.IMMUTABLE_FIELDS.intersection(kwargs):
            raise ValidationError('SOP version content is immutable; create a new version instead.')
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        if self.IMMUTABLE_FIELDS.intersection(fields):
            raise ValidationError('SOP version content is immutable; create a new version instead.')
        return super().bulk_update(objs, fields, **kwargs)

    def delete(self):
        raise ValidationError('SOP versions cannot be deleted.')


class SOP(models.Model):
    """A logical SOP in one workspace; each content change is a new version."""

    workspace = models.ForeignKey(
        'workspaces.Workspace',
        on_delete=models.PROTECT,
        related_name='sops',
    )
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=150, blank=True)
    is_archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_sops',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('title', 'pk')
        indexes = [models.Index(fields=('workspace', 'is_archived'))]

    def __str__(self):
        return self.title

    @property
    def latest_version(self):
        return self.versions.order_by('-version_number').first()


class SOPVersion(models.Model):
    """An immutable snapshot of canonical SOP text."""

    class ProcessingStatus(models.TextChoices):
        STORED = 'stored', 'Stored; not prepared for retrieval'
        PREPARING = 'preparing', 'Preparing for retrieval'
        READY = 'ready', 'Ready for retrieval'
        FAILED = 'failed', 'Preparation failed'

    sop = models.ForeignKey(
        SOP,
        on_delete=models.PROTECT,
        related_name='versions',
    )
    version_number = models.PositiveIntegerField()
    version_label = models.CharField(max_length=30)
    canonical_text = models.TextField()
    content_hash = models.CharField(max_length=64, editable=False)
    processing_status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.STORED,
        editable=False,
    )
    preparation_error = models.TextField(blank=True, editable=False)
    embedding_model = models.CharField(max_length=255, blank=True, editable=False)
    embedding_dimensions = models.PositiveSmallIntegerField(default=768, editable=False)
    active_preparation_key = models.CharField(max_length=64, blank=True, editable=False)
    prepared_at = models.DateTimeField(null=True, blank=True, editable=False)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='uploaded_sop_versions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SOPVersionQuerySet.as_manager()

    class Meta:
        ordering = ('sop_id', 'version_number')
        constraints = [
            models.UniqueConstraint(
                fields=('sop', 'version_number'),
                name='unique_sop_version_number',
            ),
            models.UniqueConstraint(
                fields=('sop', 'version_label'),
                name='unique_sop_version_label',
            ),
        ]

    def __str__(self):
        return f'{self.sop.title} — {self.version_label}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            try:
                original = type(self).objects.filter(pk=self.pk).values(
                    'sop_id', 'version_number', 'version_label', 'canonical_text',
                    'content_hash', 'uploaded_by_id', 'created_at',
                ).get()
            except type(self).DoesNotExist:
                original = None
            if original is not None:
                changed = any(
                    getattr(self, field) != value
                    for field, value in original.items()
                )
                if changed:
                    raise ValidationError('SOP version content is immutable; create a new version instead.')

        from .services import content_sha256

        if self.pk is None:
            self.content_hash = content_sha256(self.canonical_text)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('SOP versions cannot be deleted.')


class SOPChunkQuerySet(models.QuerySet):
    SOURCE_FIELDS = {
        'sop_version', 'sop_version_id', 'preparation_key', 'chunk_index',
        'section_heading', 'chunk_text', 'start_char', 'end_char',
        'content_hash', 'embedding_model', 'embedding_dimensions',
    }

    def update(self, **kwargs):
        if self.SOURCE_FIELDS.intersection(kwargs):
            raise ValidationError('Canonical chunk text and locations are immutable.')
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        if self.SOURCE_FIELDS.intersection(fields):
            raise ValidationError('Canonical chunk text and locations are immutable.')
        return super().bulk_update(objs, fields, **kwargs)

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        for obj in objs:
            obj.full_clean(validate_unique=False, validate_constraints=False)
        return super().bulk_create(objs, **kwargs)

    def delete(self):
        raise ValidationError('Canonical chunks are retained so evidence references remain valid.')


class SOPChunk(models.Model):
    """A canonical, location-verified slice of one immutable SOP version."""

    sop_version = models.ForeignKey(SOPVersion, on_delete=models.PROTECT, related_name='chunks')
    preparation_key = models.CharField(max_length=64)
    chunk_index = models.PositiveIntegerField()
    section_heading = models.CharField(max_length=500, blank=True)
    chunk_text = models.TextField()
    start_char = models.PositiveIntegerField()
    end_char = models.PositiveIntegerField()
    content_hash = models.CharField(max_length=64)
    embedding = EmbeddingVectorField(dimensions=768, null=True, blank=True)
    embedding_model = models.CharField(max_length=255)
    embedding_dimensions = models.PositiveSmallIntegerField(default=768)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SOPChunkQuerySet.as_manager()

    class Meta:
        ordering = ('sop_version_id', 'preparation_key', 'chunk_index')
        constraints = [
            models.UniqueConstraint(
                fields=('sop_version', 'preparation_key', 'chunk_index'),
                name='unique_sop_chunk_in_preparation',
            ),
        ]
        indexes = [models.Index(fields=('sop_version', 'preparation_key', 'chunk_index'))]

    def clean(self):
        if not self.sop_version_id:
            return
        source = self.sop_version.canonical_text
        if self.start_char < 0 or self.end_char <= self.start_char or self.end_char > len(source):
            raise ValidationError({'start_char': 'Chunk offsets must be a non-empty half-open range within the source.'})
        if self.chunk_text != source[self.start_char:self.end_char]:
            raise ValidationError({'chunk_text': 'Chunk text must exactly match the canonical source slice.'})
        if self.embedding_dimensions != 768:
            raise ValidationError({'embedding_dimensions': 'Chunk dimension must match the configured 768-dimensional model.'})
        if self.embedding is not None:
            self.embedding = self._meta.get_field('embedding')._validated(self.embedding)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                'sop_version_id', 'preparation_key', 'chunk_index', 'section_heading',
                'chunk_text', 'start_char', 'end_char', 'content_hash',
                'embedding_model', 'embedding_dimensions',
            ).first()
            if original and any(original[field] != getattr(self, field) for field in original):
                raise ValidationError('Canonical chunk text and locations are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Canonical chunks are retained so evidence references remain valid.')

    def __str__(self):
        return f'{self.sop_version} — chunk {self.chunk_index}'
