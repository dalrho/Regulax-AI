from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class AssessmentQuerySet(models.QuerySet):
    SCOPE_FIELDS = {'workspace', 'workspace_id', 'name', 'pack_version', 'pack_version_id', 'created_by', 'created_by_id'}

    def update(self, **kwargs):
        if self.SCOPE_FIELDS.intersection(kwargs) and self.filter(
            Q(scope_frozen_at__isnull=False) | ~Q(status=Assessment.Status.DRAFT),
        ).exists():
            raise ValidationError('A started assessment scope cannot be edited; create a new assessment.')
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        if self.SCOPE_FIELDS.intersection(fields) and any(
            obj.scope_frozen_at or obj.status != Assessment.Status.DRAFT for obj in objs
        ):
            raise ValidationError('A started assessment scope cannot be edited; create a new assessment.')
        return super().bulk_update(objs, fields, **kwargs)


class Assessment(models.Model):
    """A saved assessment definition pinned to exact immutable source versions."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        QUEUED = 'queued', 'Queued'
        RUNNING = 'running', 'Running'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    workspace = models.ForeignKey('workspaces.Workspace', on_delete=models.PROTECT, related_name='assessments')
    name = models.CharField(max_length=200)
    pack_version = models.ForeignKey(
        'regulations.RequirementPackVersion', on_delete=models.PROTECT, related_name='assessments',
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assessments')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    scope_frozen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AssessmentQuerySet.as_manager()

    class Meta:
        ordering = ('-created_at', '-pk')
        indexes = [models.Index(fields=('workspace', '-created_at'))]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                'workspace_id', 'name', 'pack_version_id', 'created_by_id', 'status', 'scope_frozen_at',
            ).first()
            if original and (original['scope_frozen_at'] or original['status'] != self.Status.DRAFT):
                tracked = ('workspace_id', 'name', 'pack_version_id', 'created_by_id')
                if any(original[field] != getattr(self, field) for field in tracked):
                    raise ValidationError('A started assessment scope cannot be edited; create a new assessment.')
                if original['status'] != self.status or original['scope_frozen_at'] != self.scope_frozen_at:
                    raise ValidationError('Assessment execution state is managed by the execution service.')
        super().save(*args, **kwargs)


class AssessmentDocumentQuerySet(models.QuerySet):
    def _assert_editable(self, assessments):
        if assessments.filter(Q(scope_frozen_at__isnull=False) | ~Q(status=Assessment.Status.DRAFT)).exists():
            raise ValidationError('A started assessment scope cannot be edited; create a new assessment.')

    def update(self, **kwargs):
        self._assert_editable(Assessment.objects.filter(pk__in=self.values('assessment_id')))
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        assessment_ids = {obj.assessment_id for obj in objs}
        self._assert_editable(Assessment.objects.filter(pk__in=assessment_ids))
        return super().bulk_update(objs, fields, **kwargs)

    def bulk_create(self, objs, **kwargs):
        assessment_ids = {obj.assessment_id for obj in objs}
        self._assert_editable(Assessment.objects.filter(pk__in=assessment_ids))
        return super().bulk_create(objs, **kwargs)

    def delete(self):
        self._assert_editable(Assessment.objects.filter(pk__in=self.values('assessment_id')))
        return super().delete()


class AssessmentDocument(models.Model):
    class Role(models.TextChoices):
        PRIMARY = 'primary', 'Primary'
        SUPPORTING = 'supporting', 'Supporting'

    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name='documents')
    sop_version = models.ForeignKey('documents.SOPVersion', on_delete=models.PROTECT, related_name='assessment_selections')
    role = models.CharField(max_length=12, choices=Role.choices)
    selected_at = models.DateTimeField(auto_now_add=True)

    objects = AssessmentDocumentQuerySet.as_manager()

    class Meta:
        ordering = ('role', 'pk')
        constraints = [
            models.UniqueConstraint(fields=('assessment', 'sop_version'), name='unique_sop_version_per_assessment'),
            models.UniqueConstraint(fields=('assessment',), condition=models.Q(role='primary'), name='one_primary_per_assessment'),
        ]

    def clean(self):
        if self.assessment_id and self.sop_version_id and self.assessment.workspace_id != self.sop_version.sop.workspace_id:
            raise ValidationError({'sop_version': 'The selected SOP version is not in this assessment workspace.'})
        if self.assessment_id and (self.assessment.scope_frozen_at or self.assessment.status != Assessment.Status.DRAFT):
            raise ValidationError('A started assessment scope cannot be edited; create a new assessment.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.clean()
        return super().delete(*args, **kwargs)
