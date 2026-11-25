from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class AssessmentQuerySet(models.QuerySet):
    SCOPE_FIELDS = {
        'workspace', 'workspace_id', 'name', 'pack_version', 'pack_version_id',
        'created_by', 'created_by_id', 'execution_config',
    }

    def update(self, **kwargs):
        if {'status', 'scope_frozen_at'}.intersection(kwargs):
            raise ValidationError('Assessment execution state is managed by the execution service.')
        if self.SCOPE_FIELDS.intersection(kwargs) and self.filter(
            Q(scope_frozen_at__isnull=False) | ~Q(status=Assessment.Status.DRAFT)
            | Q(requirement_exclusions__isnull=False),
        ).exists():
            raise ValidationError('A started assessment scope cannot be edited; create a new assessment.')
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        if {'status', 'scope_frozen_at'}.intersection(fields):
            raise ValidationError('Assessment execution state is managed by the execution service.')
        if self.SCOPE_FIELDS.intersection(fields) and any(
            obj.scope_frozen_at or obj.status != Assessment.Status.DRAFT
            or RequirementExclusion.objects.filter(assessment_id=obj.pk).exists()
            for obj in objs
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
    execution_config = models.JSONField(default=dict, blank=True)
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
                'workspace_id', 'name', 'pack_version_id', 'created_by_id', 'status', 'scope_frozen_at', 'execution_config',
            ).first()
            if original:
                started = original['scope_frozen_at'] or original['status'] != self.Status.DRAFT
                has_exclusions = RequirementExclusion.objects.filter(assessment_id=self.pk).exists()
                scope_fields = ('workspace_id', 'name', 'pack_version_id', 'created_by_id')
                if (started or has_exclusions) and any(original[field] != getattr(self, field) for field in scope_fields):
                    raise ValidationError('A started assessment scope cannot be edited; create a new assessment.')
                old_status = original['status']
                new_status = self.status
                if original['execution_config'] != self.execution_config and (started or has_exclusions):
                    config_freezes_on_queue = (
                        old_status == self.Status.DRAFT
                        and new_status == self.Status.QUEUED
                        and original['execution_config'] == {}
                    )
                    if not config_freezes_on_queue:
                        raise ValidationError('A started assessment configuration cannot be edited.')
                allowed_transitions = {
                    self.Status.DRAFT: {self.Status.DRAFT, self.Status.QUEUED},
                    self.Status.QUEUED: {self.Status.QUEUED, self.Status.RUNNING, self.Status.FAILED},
                    self.Status.RUNNING: {self.Status.RUNNING, self.Status.QUEUED, self.Status.COMPLETED, self.Status.FAILED},
                    self.Status.FAILED: {self.Status.FAILED, self.Status.QUEUED},
                    self.Status.COMPLETED: {self.Status.COMPLETED},
                }
                if new_status not in allowed_transitions.get(old_status, set()):
                    raise ValidationError('Invalid assessment execution state transition.')
                if new_status != old_status:
                    required_job_status = {
                        self.Status.QUEUED: AssessmentRunJob.Status.QUEUED,
                        self.Status.RUNNING: AssessmentRunJob.Status.RUNNING,
                        self.Status.COMPLETED: AssessmentRunJob.Status.COMPLETED,
                        self.Status.FAILED: AssessmentRunJob.Status.FAILED,
                    }.get(new_status)
                    if required_job_status and not AssessmentRunJob.objects.filter(
                        assessment_id=self.pk, status=required_job_status,
                    ).exists():
                        raise ValidationError('Assessment state must match its durable run job.')
                if original['scope_frozen_at'] and original['scope_frozen_at'] != self.scope_frozen_at:
                    raise ValidationError('A frozen assessment scope cannot be changed.')
                if new_status == self.Status.QUEUED and not self.scope_frozen_at:
                    raise ValidationError('An assessment must have a frozen scope before queueing.')
        super().save(*args, **kwargs)


class AssessmentDocumentQuerySet(models.QuerySet):
    def _assert_editable(self, assessments):
        if assessments.filter(
            Q(scope_frozen_at__isnull=False) | ~Q(status=Assessment.Status.DRAFT)
            | Q(requirement_exclusions__isnull=False),
        ).exists():
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
    preparation_key = models.CharField(max_length=64, blank=True, editable=False)
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
            original = type(self).objects.filter(pk=self.pk).values(
                'assessment_id', 'sop_version_id', 'role',
            ).first() if self.pk else None
            may_pin_preparation = (
                original is not None
                and self.assessment.status == Assessment.Status.DRAFT
                and not self.assessment.execution_config
                and all(original[field] == getattr(self, field) for field in original)
            )
            if not may_pin_preparation:
                raise ValidationError('A started assessment scope cannot be edited; create a new assessment.')
        if self.assessment_id and RequirementExclusion.objects.filter(assessment_id=self.assessment_id).exists():
            original = type(self).objects.filter(pk=self.pk).values(
                'assessment_id', 'sop_version_id', 'role',
            ).first() if self.pk else None
            if original is None or any(original[field] != getattr(self, field) for field in original):
                raise ValidationError('An assessment with applicability exclusions cannot be edited; create a new assessment.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.clean()
        return super().delete(*args, **kwargs)


class ImmutableRecordQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Assessment findings and verified evidence are immutable.')

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError('Assessment findings and verified evidence are immutable.')

    def delete(self):
        raise ValidationError('Assessment findings and verified evidence are retained.')

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        for obj in objs:
            obj.full_clean()
        return super().bulk_create(objs, **kwargs)


class Finding(models.Model):
    """One validated, immutable model result for one pinned requirement."""

    class Category(models.TextChoices):
        ADDRESSED = 'addressed', 'Addressed in submitted text'
        PARTIALLY_ADDRESSED = 'partially_addressed', 'Partially addressed'
        POTENTIAL_CONFLICT = 'potential_conflict', 'Potential conflict'
        NOT_FOUND = 'not_found', 'Not found in assessed documents'
        UNABLE_TO_DETERMINE = 'unable_to_determine', 'Unable to determine'

    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name='findings')
    requirement = models.ForeignKey('regulations.Requirement', on_delete=models.PROTECT, related_name='findings')
    category = models.CharField(max_length=32, choices=Category.choices)
    explanation = models.TextField()
    model_output = models.JSONField()
    raw_model_output = models.TextField(blank=True)
    provider_name = models.CharField(max_length=100)
    model_name = models.CharField(max_length=200, blank=True)
    review_scope = models.CharField(max_length=20, choices=(('focused', 'Focused retrieval'), ('comprehensive', 'Broader review'), ('unavailable', 'Broader review unavailable')))
    scope_summary = models.TextField(blank=True)
    scope_limitations = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('requirement__sort_order', 'requirement__code', 'pk')
        constraints = [
            models.UniqueConstraint(fields=('assessment', 'requirement'), name='one_finding_per_assessment_requirement'),
        ]

    objects = ImmutableRecordQuerySet.as_manager()

    def clean(self):
        if self.assessment_id and self.requirement_id:
            if self.requirement.pack_version_id != self.assessment.pack_version_id:
                raise ValidationError({'requirement': 'Finding requirement must belong to the pinned assessment pack version.'})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Findings are immutable; create a new assessment to rerun a requirement.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Findings are retained as immutable assessment records.')


class FindingEvidence(models.Model):
    """A backend-verified quotation and offset into one selected SOP version."""

    class Kind(models.TextChoices):
        SUPPORTING = 'supporting', 'Supporting evidence'
        LIMITATION = 'limitation', 'Evidence limitation'

    finding = models.ForeignKey(Finding, on_delete=models.CASCADE, related_name='evidence')
    chunk = models.ForeignKey('documents.SOPChunk', on_delete=models.PROTECT, related_name='finding_evidence')
    sop_version = models.ForeignKey('documents.SOPVersion', on_delete=models.PROTECT, related_name='finding_evidence')
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.SUPPORTING)
    start_char = models.PositiveIntegerField()
    end_char = models.PositiveIntegerField()
    quote = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('sop_version_id', 'start_char', 'pk')

    objects = ImmutableRecordQuerySet.as_manager()

    def clean(self):
        if not self.finding_id or not self.chunk_id or not self.sop_version_id:
            return
        source = self.sop_version.canonical_text
        if self.chunk.sop_version_id != self.sop_version_id:
            raise ValidationError({'chunk': 'Evidence chunk must belong to the cited SOP version.'})
        if not self.finding.assessment.documents.filter(sop_version_id=self.sop_version_id).exists():
            raise ValidationError({'sop_version': 'Evidence must come from a selected version in this assessment.'})
        selected_document = self.finding.assessment.documents.get(sop_version_id=self.sop_version_id)
        expected_key = selected_document.preparation_key or selected_document.sop_version.active_preparation_key
        if expected_key and self.chunk.preparation_key != expected_key:
            raise ValidationError({'chunk': 'Evidence chunk must belong to the preparation set pinned for this assessment.'})
        if self.start_char < self.chunk.start_char or self.end_char > self.chunk.end_char or self.end_char <= self.start_char:
            raise ValidationError({'start_char': 'Evidence offsets must be within the cited source chunk.'})
        if source[self.start_char:self.end_char] != self.quote:
            raise ValidationError({'quote': 'Evidence quotation must exactly match the canonical source text.'})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Verified evidence is immutable.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Verified evidence is retained with its finding.')


class FindingReviewQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Reviewer decisions are append-only.')

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError('Reviewer decisions are append-only.')

    def delete(self):
        raise ValidationError('Reviewer decisions are append-only.')

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        for obj in objs:
            obj.full_clean()
        return super().bulk_create(objs, **kwargs)


class RequirementExclusionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Applicability exclusions are append-only.')

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError('Applicability exclusions are append-only.')

    def delete(self):
        raise ValidationError('Applicability exclusions are append-only.')

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        for obj in objs:
            obj.full_clean()
        return super().bulk_create(objs, **kwargs)


class FindingReview(models.Model):
    class Decision(models.TextChoices):
        ACCEPT = 'accepted', 'Accepted by reviewer'
        REJECT = 'rejected', 'Rejected by reviewer'
        NEEDS_REVISION = 'needs_further_review', 'Needs further review'

    finding = models.ForeignKey(Finding, on_delete=models.PROTECT, related_name='reviews')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='finding_reviews')
    decision = models.CharField(max_length=20, choices=Decision.choices)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = FindingReviewQuerySet.as_manager()

    class Meta:
        ordering = ('created_at', 'pk')

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Reviewer decisions are append-only.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Reviewer decisions are append-only.')


class AssessmentRunJob(models.Model):
    """Durable queue and lease state for one frozen assessment run."""

    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        RUNNING = 'running', 'Running'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    assessment = models.OneToOneField(Assessment, on_delete=models.CASCADE, related_name='run_job')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=('status', 'next_attempt_at', 'lease_expires_at'))]


class RequirementExecution(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        RUNNING = 'running', 'Running'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        EXCLUDED = 'excluded', 'Excluded by reviewer'

    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name='requirement_executions')
    requirement = models.ForeignKey('regulations.Requirement', on_delete=models.PROTECT, related_name='executions')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('requirement__sort_order', 'requirement__code', 'pk')
        constraints = [
            models.UniqueConstraint(fields=('assessment', 'requirement'), name='one_execution_per_assessment_requirement'),
        ]

    def clean(self):
        if self.assessment_id and self.requirement_id:
            if self.requirement.pack_version_id != self.assessment.pack_version_id:
                raise ValidationError({'requirement': 'Execution requirement must belong to the pinned pack version.'})


class RequirementExecutionFailureQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Execution failure records are append-only.')

    def delete(self):
        raise ValidationError('Execution failure records are append-only.')

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError('Execution failure records are append-only.')


class RequirementExecutionFailure(models.Model):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name='execution_failures')
    requirement = models.ForeignKey('regulations.Requirement', on_delete=models.PROTECT, related_name='execution_failures')
    attempt_number = models.PositiveSmallIntegerField()
    error_type = models.CharField(max_length=100)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = RequirementExecutionFailureQuerySet.as_manager()

    class Meta:
        ordering = ('created_at', 'pk')

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Execution failure records are append-only.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Execution failure records are append-only.')


class RequirementExclusion(models.Model):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name='requirement_exclusions')
    requirement = models.ForeignKey('regulations.Requirement', on_delete=models.PROTECT, related_name='assessment_exclusions')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='requirement_exclusions')
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'pk')
        constraints = [
            models.UniqueConstraint(fields=('assessment', 'requirement'), name='one_exclusion_per_assessment_requirement'),
        ]

    objects = RequirementExclusionQuerySet.as_manager()

    def clean(self):
        if self.assessment_id and self.requirement_id:
            if self.requirement.pack_version_id != self.assessment.pack_version_id:
                raise ValidationError({'requirement': 'Exclusion requirement must belong to the pinned pack version.'})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Applicability exclusions are append-only.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Applicability exclusions are append-only.')
