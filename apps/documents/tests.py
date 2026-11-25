from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from apps.workspaces.models import Membership, Workspace

from .chunking import chunk_text
from .embeddings import DeterministicEmbeddingProvider, EmbeddingError
from .models import SOP, SOPChunk, SOPVersion
from .preparation import PreparationError, prepare_sop_version, retrieve_evidence


class ChunkingTests(TestCase):
    def test_offsets_reproduce_unicode_multiline_source_exactly(self):
        source = '  # Procédure 🧪\nligne deux\n\nÉTAPE 1\nUne action.\n\nlast para  '
        specs = chunk_text(source, max_chars=100)
        self.assertTrue(specs)
        for spec in specs:
            self.assertEqual(spec.chunk_text, source[spec.start_char:spec.end_char])
            self.assertGreater(spec.end_char, spec.start_char)
        self.assertIn('Procédure 🧪\nligne deux', specs[0].chunk_text)


class PreparationAndRetrievalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='member', password='password')
        self.workspace = Workspace.objects.create(company_name='Workspace A')
        Membership.objects.create(user=self.user, workspace=self.workspace, role=Membership.Role.OWNER)
        self.provider = DeterministicEmbeddingProvider(dimensions=768)

    def make_version(self, *, title, text, workspace=None):
        workspace = workspace or self.workspace
        sop = SOP.objects.create(workspace=workspace, title=title, created_by=self.user)
        return SOPVersion.objects.create(
            sop=sop, version_number=1, version_label='v1', canonical_text=text, uploaded_by=self.user,
        )

    def test_preparation_is_idempotent_and_offsets_validate(self):
        source = '## Checks\n\nKeep the room 🧪 clean.\n\nRecord the result.\n'
        version = self.make_version(title='Checks', text=source)
        first = prepare_sop_version(
            user=self.user, workspace=self.workspace, version_id=version.pk, provider=self.provider,
        )
        first_ids = list(first.chunks.values_list('pk', flat=True))
        second = prepare_sop_version(
            user=self.user, workspace=self.workspace, version_id=version.pk, provider=self.provider,
        )
        chunks = list(second.chunks.filter(preparation_key=second.active_preparation_key))
        self.assertEqual(second.processing_status, SOPVersion.ProcessingStatus.READY)
        self.assertEqual(first_ids, [chunk.pk for chunk in chunks])
        for chunk in chunks:
            self.assertEqual(chunk.chunk_text, source[chunk.start_char:chunk.end_char])

    def test_new_preparation_key_preserves_old_chunk_references(self):
        version = self.make_version(
            title='Versioned chunks',
            text='First paragraph has enough text to split into separate chunks.\n\n'
                 'Second paragraph keeps another stable evidence location.',
        )
        first = prepare_sop_version(
            user=self.user, workspace=self.workspace, version_id=version.pk, provider=self.provider,
        )
        old_ids = set(first.chunks.values_list('pk', flat=True))
        with override_settings(SOP_CHUNK_MAX_CHARS=24):
            second = prepare_sop_version(
                user=self.user, workspace=self.workspace, version_id=version.pk, provider=self.provider,
            )
        new_ids = set(second.chunks.filter(preparation_key=second.active_preparation_key).values_list('pk', flat=True))
        self.assertNotEqual(first.active_preparation_key, second.active_preparation_key)
        self.assertTrue(old_ids.isdisjoint(new_ids))
        self.assertTrue(old_ids.issubset(set(SOPChunk.objects.filter(sop_version=version).values_list('pk', flat=True))))

    def test_embedding_error_does_not_mark_version_ready(self):
        version = self.make_version(title='Failure', text='A non-empty paragraph.')

        class BrokenProvider:
            model_name = 'broken-test-provider'
            dimensions = 768

            def embed_documents(self, texts):
                raise EmbeddingError('test embedding failure')

        with self.assertRaises(PreparationError):
            prepare_sop_version(
                user=self.user, workspace=self.workspace, version_id=version.pk, provider=BrokenProvider(),
            )
        version.refresh_from_db()
        self.assertEqual(version.processing_status, SOPVersion.ProcessingStatus.FAILED)
        self.assertIn('test embedding failure', version.preparation_error)
        self.assertFalse(SOPChunk.objects.filter(sop_version=version).exists())

    def test_keyword_and_semantic_search_can_return_supporting_sop(self):
        primary = self.make_version(title='Primary', text='General record keeping procedure.')
        supporting = self.make_version(
            title='Supporting sanitation',
            text='## Sanitation verification\n\nBlueberry equipment requires sanitation verification after cleaning.\n\nDocument the signature.',
        )
        for version in (primary, supporting):
            prepare_sop_version(
                user=self.user, workspace=self.workspace, version_id=version.pk, provider=self.provider,
            )
        result = retrieve_evidence(
            user=self.user,
            workspace=self.workspace,
            selected_version_ids=[primary.pk, supporting.pk],
            query='blueberry sanitation verification',
            top_k=10,
            provider=self.provider,
        )
        self.assertTrue(result.semantic_available)
        self.assertTrue(any('semantic' in item.matched_by for item in result.results))
        supporting_hit = next(item for item in result.results if item.chunk.sop_version_id == supporting.pk)
        self.assertIn('keyword', supporting_hit.matched_by)
        self.assertIn('Blueberry equipment', supporting_hit.passage_text)
        self.assertEqual(supporting_hit.chunk.sop_version.sop.title, 'Supporting sanitation')

    def test_queries_reject_versions_from_a_different_workspace(self):
        other = Workspace.objects.create(company_name='Workspace B')
        foreign_version = self.make_version(title='Foreign', text='Foreign SOP text.', workspace=other)
        with self.assertRaises(PermissionDenied):
            retrieve_evidence(
                user=self.user, workspace=self.workspace,
                selected_version_ids=[foreign_version.pk], query='SOP', provider=self.provider,
            )

    @override_settings(SOP_CHUNK_MAX_CHARS=70)
    def test_retrieval_includes_neighboring_source_chunks(self):
        source = (
            'Preceding paragraph with enough words to become its own chunk.\n\n'
            'Target compliance evidence appears in this paragraph.\n\n'
            'Following paragraph supplies neighboring context after the target.'
        )
        version = self.make_version(title='Neighbors', text=source)
        prepare_sop_version(
            user=self.user, workspace=self.workspace, version_id=version.pk, provider=self.provider,
        )
        result = retrieve_evidence(
            user=self.user, workspace=self.workspace, selected_version_ids=[version.pk],
            query='target compliance evidence', top_k=1, neighbor_chunks=1, provider=self.provider,
        )
        hit = result.results[0]
        self.assertGreater(len(hit.context_chunk_ids), 1)
        self.assertEqual(hit.passage_text, source[hit.passage_start:hit.passage_end])
        self.assertIn('Preceding paragraph', hit.passage_text)
        self.assertIn('Following paragraph', hit.passage_text)
