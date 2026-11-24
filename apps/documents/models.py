from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class SOPVersionQuerySet(models.QuerySet):
    IMMUTABLE_FIELDS = {
        'sop', 'sop_id', 'version_number', 'version_label',
        'canonical_text', 'content_hash', 'uploaded_by', 'uploaded_by_id',
        'created_at',
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
