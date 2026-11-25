from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from apps.documents.embeddings import DeterministicEmbeddingProvider
from apps.documents.models import SOP, SOPVersion
from apps.documents.preparation import prepare_sop_version
from apps.regulations.models import Requirement, RequirementPack, RequirementPackVersion
from apps.regulations.services import publish_pack_version, record_pack_review
from apps.workspaces.models import Membership, Workspace

from .execution import claim_next_job, exclude_requirement, queue_assessment, retry_assessment, run_claimed_job
from .finding_service import FindingAssessmentError, StaleWorkerError, assess_requirement
from .models import (
    Assessment,
    AssessmentDocument,
    AssessmentRunJob,
    Finding,
    FindingEvidence,
    FindingReview,
    RequirementExecution,
    RequirementExecutionFailure,
)
from .providers import DeterministicProvider, ProviderError
from .services import record_finding_review


class StaticProvider:
    name = 'static-test-provider'
    model_name = 'static-test-model'

    def __init__(self, responses):
        self.responses = list(responses)
        self.scopes = []

    def generate_finding(self, *, requirement, evidence, review_scope):
        self.scopes.append((review_scope, len(evidence)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _AssessmentFixture:
    source = (
        'Batch review procedure\n\n'
        'The batch operator records each completed batch review in the controlled log.\n\n'
        'A supervisor verifies the log before release.'
    )

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='qa-member', password='pw')
        self.workspace = Workspace.objects.create(company_name='QA Workspace')
        Membership.objects.create(user=self.user, workspace=self.workspace, role=Membership.Role.OWNER)
        self.pack = RequirementPack.objects.create(
            slug='qa-pack', title='QA pack', jurisdiction='US', created_by=self.user,
        )
        self.pack_version = RequirementPackVersion.objects.create(
            pack=self.pack, version_number=1, version_label='v1', created_by=self.user,
        )
        self.requirement = Requirement.objects.create(
            pack_version=self.pack_version,
            code='QA-1',
            title='Batch review record',
            citation='21 CFR 211.192',
            original_text='A record shall include review of production records.',
            assessment_question='Does the SOP require review to be recorded?',
        )
        self.sop = SOP.objects.create(
            workspace=self.workspace, title='Batch Review', created_by=self.user,
        )
        self.version = SOPVersion.objects.create(
            sop=self.sop, version_number=1, version_label='v1',
            canonical_text=self.source, uploaded_by=self.user,
        )
        self.embedding_provider = DeterministicEmbeddingProvider(dimensions=768)
        self.version = prepare_sop_version(
            user=self.user, workspace=self.workspace, version_id=self.version.pk,
            provider=self.embedding_provider,
        )
        self.assessment = Assessment.objects.create(
            workspace=self.workspace, name='Batch review check',
            pack_version=self.pack_version, created_by=self.user,
        )
        AssessmentDocument.objects.create(
            assessment=self.assessment, sop_version=self.version,
            role=AssessmentDocument.Role.PRIMARY,
        )
        self.quote = 'records each completed batch review'

    def output(self, category='addressed', *, quote=None, chunk=None, version=None, before=None, after=None):
        chunk = chunk or self.version.chunks.get(preparation_key=self.version.active_preparation_key)
        quote = self.quote if quote is None else quote
        offset = chunk.chunk_text.find(quote)
        if before is None:
            before = chunk.chunk_text[max(0, offset - 28):offset] if offset >= 0 else ''
        if after is None:
            after = chunk.chunk_text[offset + len(quote):offset + len(quote) + 28] if offset >= 0 else ''
        citations = []
        if category in ('addressed', 'partially_addressed', 'potential_conflict') or quote:
            citations.append({
                'chunk_id': chunk.pk,
                'version_id': version.pk if version else self.version.pk,
                'quote': quote,
                'context_before': before,
                'context_after': after,
            })
        return {
            'category': category,
            'explanation': 'The cited SOP text is relevant to the requirement.',
            'citations': citations,
            'limitations': [],
        }

    def assessment_call(self, provider):
        return assess_requirement(
            assessment=self.assessment,
            requirement=self.requirement,
            provider=provider,
            retrieval_provider=self.embedding_provider,
        )

class RequirementFindingTests(_AssessmentFixture, TestCase):
    def test_valid_quote_is_saved_with_backend_resolved_offsets(self):
        provider = StaticProvider([self.output()])
        finding = self.assessment_call(provider)
        evidence = finding.evidence.get()
        self.assertEqual(finding.category, Finding.Category.ADDRESSED)
        self.assertEqual(evidence.quote, self.quote)
        self.assertEqual(self.version.canonical_text[evidence.start_char:evidence.end_char], self.quote)
        self.assertEqual(finding.requirement_id, self.requirement.pk)
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.status, Assessment.Status.DRAFT)
        self.assertIsNotNone(self.assessment.scope_frozen_at)

    def test_fabricated_quote_is_a_technical_failure_not_a_finding(self):
        provider = StaticProvider([self.output(quote='fabricated quotation'), self.output(quote='fabricated quotation')])
        with self.assertRaises(FindingAssessmentError):
            self.assessment_call(provider)
        self.assertEqual(len(provider.scopes), 2)
        self.assertFalse(Finding.objects.filter(assessment=self.assessment).exists())
        self.assertEqual(RequirementExecutionFailure.objects.filter(assessment=self.assessment).count(), 1)
        self.assertEqual(
            RequirementExecution.objects.get(assessment=self.assessment, requirement=self.requirement).status,
            RequirementExecution.Status.FAILED,
        )

    def test_not_found_requires_comprehensive_review_and_has_no_highlight(self):
        response = self.output(category='not_found', quote='')
        provider = StaticProvider([response, response])
        finding = self.assessment_call(provider)
        self.assertEqual([scope for scope, _count in provider.scopes], ['focused', 'comprehensive'])
        self.assertEqual(finding.category, Finding.Category.NOT_FOUND)
        self.assertEqual(finding.review_scope, 'comprehensive')
        self.assertFalse(finding.evidence.exists())

    def test_not_found_becomes_unable_when_full_selected_scope_exceeds_review_limit(self):
        long_text = 'Batch review procedure records controlled production activities. ' * 1100
        version = SOPVersion.objects.create(
            sop=SOP.objects.create(workspace=self.workspace, title='Long procedure', created_by=self.user),
            version_number=1, version_label='v1', canonical_text=long_text, uploaded_by=self.user,
        )
        version = prepare_sop_version(
            user=self.user, workspace=self.workspace, version_id=version.pk,
            provider=self.embedding_provider,
        )
        self.assessment.documents.filter(role=AssessmentDocument.Role.PRIMARY).delete()
        AssessmentDocument.objects.create(
            assessment=self.assessment, sop_version=version, role=AssessmentDocument.Role.PRIMARY,
        )
        provider = StaticProvider([self.output(category='not_found', quote='')])
        finding = self.assessment_call(provider)
        self.assertEqual(finding.category, Finding.Category.UNABLE_TO_DETERMINE)
        self.assertEqual(finding.review_scope, 'unavailable')
        self.assertEqual(len(provider.scopes), 1)
        self.assertTrue(finding.scope_limitations)
        self.assertFalse(finding.evidence.exists())

    def test_ambiguous_repeated_quote_is_rejected_without_guessing(self):
        repeated_text = 'Repeated action phrase.\n\nRepeated action phrase.'
        version = SOPVersion.objects.create(
            sop=SOP.objects.create(workspace=self.workspace, title='Repeated', created_by=self.user),
            version_number=1, version_label='v1', canonical_text=repeated_text, uploaded_by=self.user,
        )
        version = prepare_sop_version(user=self.user, workspace=self.workspace, version_id=version.pk, provider=self.embedding_provider)
        self.assessment.documents.filter(role=AssessmentDocument.Role.PRIMARY).delete()
        AssessmentDocument.objects.create(assessment=self.assessment, sop_version=version, role=AssessmentDocument.Role.PRIMARY)
        repeated = version.chunks.get(preparation_key=version.active_preparation_key)
        response = self.output(quote='Repeated action phrase.', chunk=repeated, version=version, before='', after='')
        provider = StaticProvider([response, response])
        with self.assertRaises(FindingAssessmentError):
            self.assessment_call(provider)
        self.assertFalse(Finding.objects.filter(assessment=self.assessment).exists())

    def test_unavailable_sop_reference_is_explicit_and_limits_the_category(self):
        # SOP versions are immutable, so create a new selected version for this case.
        replacement = SOPVersion.objects.create(
            sop=self.sop, version_number=2, version_label='v2',
            canonical_text=self.source + '\n\nFor details, see SOP-999.', uploaded_by=self.user,
        )
        replacement = prepare_sop_version(user=self.user, workspace=self.workspace, version_id=replacement.pk, provider=self.embedding_provider)
        self.assessment.documents.filter(role=AssessmentDocument.Role.PRIMARY).delete()
        AssessmentDocument.objects.create(assessment=self.assessment, sop_version=replacement, role=AssessmentDocument.Role.PRIMARY)
        replacement_chunk = replacement.chunks.filter(
            preparation_key=replacement.active_preparation_key,
        ).get(chunk_text__contains=self.quote)
        response = self.output(chunk=replacement_chunk, version=replacement)
        finding = self.assessment_call(StaticProvider([response]))
        self.assertEqual(finding.category, Finding.Category.UNABLE_TO_DETERMINE)
        self.assertTrue(any('SOP-999' in item for item in finding.scope_limitations))
        limitation = finding.evidence.get(kind=FindingEvidence.Kind.LIMITATION)
        self.assertEqual(limitation.quote, 'SOP-999')

    def test_reviewer_decisions_append_without_changing_original_output(self):
        finding = self.assessment_call(DeterministicProvider())
        original_output = finding.model_output.copy()
        record_finding_review(
            user=self.user, workspace=self.workspace, finding_id=finding.pk,
            decision=FindingReview.Decision.ACCEPT, note='Checked the source.',
        )
        record_finding_review(
            user=self.user, workspace=self.workspace, finding_id=finding.pk,
            decision=FindingReview.Decision.NEEDS_REVISION, note='Please confirm the scope.',
        )
        finding.refresh_from_db()
        self.assertEqual(finding.model_output, original_output)
        self.assertEqual(finding.reviews.count(), 2)
        with self.assertRaises(ValidationError):
            FindingReview.objects.filter(finding=finding).update(note='overwrite')
        with self.assertRaises(ValidationError):
            FindingEvidence.objects.filter(finding=finding).delete()

    def test_result_detail_shows_verified_highlight_and_keeps_review_history(self):
        finding = self.assessment_call(StaticProvider([self.output()]))
        self.client.force_login(self.user)
        response = self.client.get(reverse('finding_detail', args=(self.assessment.pk, finding.pk)))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'<mark>{self.quote}</mark>', html=True)
        self.assertContains(response, f'?version={self.version.pk}#version-{self.version.pk}', html=False)
        response = self.client.post(
            reverse('finding_review', args=(self.assessment.pk, finding.pk)),
            {'decision': FindingReview.Decision.ACCEPT, 'note': 'Verified against the selected version.'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(finding.reviews.count(), 1)


class DurableAssessmentWorkerTests(_AssessmentFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.second_requirement = Requirement.objects.create(
            pack_version=self.pack_version, code='QA-2', title='Log verification',
            citation='21 CFR 211.100', original_text='Verify records.',
            assessment_question='Is a verifier named?', sort_order=1,
        )
        self.user.is_staff = True
        self.user.save(update_fields=('is_staff',))
        record_pack_review(user=self.user, version_id=self.pack_version.pk, review_notes='Reviewed test fixture.')
        self.pack_version.refresh_from_db()
        publish_pack_version(user=self.user, version_id=self.pack_version.pk)
        self.pack_version.refresh_from_db()

    def queue(self):
        return queue_assessment(
            user=self.user, workspace=self.workspace, assessment_id=self.assessment.pk,
        )

    def test_complete_job_accounts_every_requirement_and_duplicate_run_is_rejected(self):
        self.queue()
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.status, Assessment.Status.QUEUED)
        self.assertEqual(self.assessment.execution_config['prompt_version'], 'requirement-assessment-v1')
        with self.assertRaises(ValidationError):
            self.queue()
        job = claim_next_job()
        self.assertIsNotNone(job)
        outcome = run_claimed_job(
            job=job, provider=DeterministicProvider(), retrieval_provider=self.embedding_provider,
        )
        self.assertEqual(outcome, AssessmentRunJob.Status.COMPLETED)
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.status, Assessment.Status.COMPLETED)
        self.assertEqual(Finding.objects.filter(assessment=self.assessment).count(), 2)
        self.assertEqual(Finding.objects.get(requirement=self.second_requirement).category, Finding.Category.UNABLE_TO_DETERMINE)
        self.assertEqual(claim_next_job(), None)

    def test_human_exclusion_is_counted_separately_from_assessed_findings(self):
        exclusion = exclude_requirement(
            user=self.user, workspace=self.workspace, assessment_id=self.assessment.pk,
            requirement_id=self.second_requirement.pk, reason='This workspace does not use the process.',
        )
        self.queue()
        job = claim_next_job()
        outcome = run_claimed_job(
            job=job, provider=DeterministicProvider(), retrieval_provider=self.embedding_provider,
        )
        self.assessment.refresh_from_db()
        self.assertEqual(outcome, AssessmentRunJob.Status.COMPLETED)
        self.assertEqual(self.assessment.findings.count(), 1)
        self.assertEqual(self.assessment.requirement_exclusions.count(), 1)
        self.assertEqual(exclusion.reviewer, self.user)
        self.assertEqual(exclusion.reason, 'This workspace does not use the process.')

    def test_one_requirement_finding_can_resume_in_the_complete_pack_worker(self):
        finding = self.assessment_call(StaticProvider([self.output()]))
        self.queue()
        job = claim_next_job()
        state = run_claimed_job(
            job=job, provider=DeterministicProvider(), retrieval_provider=self.embedding_provider,
        )
        self.assertEqual(state, AssessmentRunJob.Status.COMPLETED)
        self.assertEqual(Finding.objects.filter(assessment=self.assessment).count(), 2)
        self.assertEqual(
            Finding.objects.get(assessment=self.assessment, requirement=self.requirement).pk,
            finding.pk,
        )

    def test_development_provider_result_cannot_be_counted_in_a_live_pack_run(self):
        self.assessment_call(DeterministicProvider())
        with self.assertRaises(ValidationError):
            self.queue()

    def test_queued_run_keeps_the_preparation_set_pinned_at_the_first_finding(self):
        finding = self.assessment_call(StaticProvider([self.output()]))
        selected_document = self.assessment.documents.get(role=AssessmentDocument.Role.PRIMARY)
        pinned_key = selected_document.preparation_key
        with override_settings(SOP_CHUNK_MAX_CHARS=24):
            prepare_sop_version(
                user=self.user, workspace=self.workspace, version_id=self.version.pk,
                provider=self.embedding_provider,
            )
        self.version.refresh_from_db()
        self.assertNotEqual(self.version.active_preparation_key, pinned_key)
        self.queue()
        job = claim_next_job()
        state = run_claimed_job(
            job=job, provider=DeterministicProvider(), retrieval_provider=self.embedding_provider,
        )
        self.assertEqual(state, AssessmentRunJob.Status.COMPLETED)
        finding.refresh_from_db()
        self.assertEqual(finding.evidence.get().chunk.preparation_key, pinned_key)
        self.assertEqual(
            self.assessment.findings.get(requirement=self.second_requirement).category,
            Finding.Category.UNABLE_TO_DETERMINE,
        )

    def test_expired_lease_is_reclaimed_and_old_worker_is_stale(self):
        self.queue()
        first = claim_next_job()
        first_token = first.lease_token
        AssessmentRunJob.objects.filter(pk=first.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        recovered = claim_next_job()
        self.assertNotEqual(first_token, recovered.lease_token)
        with self.assertRaises(StaleWorkerError):
            assess_requirement(
                assessment=self.assessment, requirement=self.requirement,
                provider=DeterministicProvider(), retrieval_provider=self.embedding_provider,
                job=first, lease_token=first_token,
            )

    def test_provider_failure_stays_an_execution_failure(self):
        class BrokenProvider:
            name = 'broken-provider'
            model_name = 'broken'

            def generate_finding(self, **_kwargs):
                raise ProviderError('provider unavailable')

        self.queue()
        job = claim_next_job()
        state = run_claimed_job(
            job=job, provider=BrokenProvider(), retrieval_provider=self.embedding_provider,
        )
        self.assertEqual(state, AssessmentRunJob.Status.QUEUED)
        self.assertFalse(Finding.objects.filter(assessment=self.assessment).exists())
        self.assertTrue(RequirementExecutionFailure.objects.filter(message__contains='provider unavailable').exists())
        self.assertEqual(
            RequirementExecution.objects.get(
                assessment=self.assessment, requirement=self.requirement,
            ).status,
            RequirementExecution.Status.FAILED,
        )

    def test_retry_resumes_valid_partial_findings_without_duplicates(self):
        class PartialProvider:
            name = 'partial-test-provider'
            model_name = 'partial-test-model'

            def generate_finding(self, *, requirement, **_kwargs):
                if requirement['code'] == 'QA-2':
                    raise ProviderError('temporary provider outage')
                return {
                    'category': 'unable_to_determine',
                    'explanation': 'A test provider response with no source citations.',
                    'citations': [],
                    'limitations': [],
                }

        self.queue()
        job = claim_next_job()
        state = run_claimed_job(job=job, provider=PartialProvider(), retrieval_provider=self.embedding_provider)
        self.assertEqual(state, AssessmentRunJob.Status.QUEUED)
        first_finding = Finding.objects.get(assessment=self.assessment, requirement=self.requirement)
        for expected_state in (AssessmentRunJob.Status.QUEUED, AssessmentRunJob.Status.FAILED):
            AssessmentRunJob.objects.filter(assessment=self.assessment).update(next_attempt_at=timezone.now())
            job = claim_next_job()
            state = run_claimed_job(job=job, provider=PartialProvider(), retrieval_provider=self.embedding_provider)
            self.assertEqual(state, expected_state)
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.status, Assessment.Status.FAILED)
        self.assertEqual(Finding.objects.filter(assessment=self.assessment).count(), 1)
        retry_assessment(user=self.user, workspace=self.workspace, assessment_id=self.assessment.pk)
        job = claim_next_job()
        run_claimed_job(job=job, provider=DeterministicProvider(), retrieval_provider=self.embedding_provider)
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.status, Assessment.Status.COMPLETED)
        self.assertEqual(Finding.objects.filter(assessment=self.assessment).count(), 2)
        self.assertEqual(
            Finding.objects.get(assessment=self.assessment, requirement=self.requirement).pk,
            first_finding.pk,
        )

    def test_run_button_queues_without_calling_a_provider_in_the_request(self):
        assessment = Assessment.objects.create(
            workspace=self.workspace, name='UI queued check',
            pack_version=self.pack_version, created_by=self.user,
        )
        AssessmentDocument.objects.create(
            assessment=assessment, sop_version=self.version, role=AssessmentDocument.Role.PRIMARY,
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse('assessment_run', args=(assessment.pk,)))
        self.assertEqual(response.status_code, 302)
        assessment.refresh_from_db()
        self.assertEqual(assessment.status, Assessment.Status.QUEUED)
        self.assertTrue(AssessmentRunJob.objects.filter(assessment=assessment).exists())
