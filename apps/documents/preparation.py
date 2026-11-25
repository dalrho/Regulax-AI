import hashlib
import math
import re
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.workspaces.models import Membership

from .chunking import CHUNKER_VERSION, chunk_text
from .embeddings import EmbeddingError, SentenceTransformersProvider
from .models import SOPChunk, SOPVersion
from .services import content_sha256


class PreparationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievedEvidence:
    chunk: SOPChunk
    score: float
    matched_by: tuple[str, ...]
    passage_text: str
    passage_start: int
    passage_end: int
    context_chunk_ids: tuple[int, ...]


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    workspace_id: int
    selected_version_ids: tuple[int, ...]
    results: tuple[RetrievedEvidence, ...]
    semantic_available: bool
    semantic_warning: str = ''


def _preparation_key(version, model_name, dimensions, max_chars):
    payload = '|'.join((
        CHUNKER_VERSION, str(max_chars), version.content_hash,
        model_name, str(dimensions),
    ))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _require_membership(user, workspace):
    if not getattr(user, 'is_authenticated', False) or not Membership.objects.filter(
        user=user, workspace=workspace,
    ).exists():
        raise PermissionDenied('Workspace membership is required to prepare or search SOP versions.')


def _validate_vectors(vectors, expected_count, dimensions):
    if len(vectors) != expected_count:
        raise EmbeddingError('Embedding provider returned a different number of vectors than requested.')
    normalized = []
    for vector in vectors:
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as error:
            raise EmbeddingError('Embedding provider returned a non-numeric vector.') from error
        if len(values) != dimensions or not all(math.isfinite(value) for value in values):
            raise EmbeddingError(f'Embedding provider must return finite vectors of dimension {dimensions}.')
        normalized.append(values)
    return normalized


def _failure_message(error):
    if isinstance(error, (EmbeddingError, PreparationError, ValidationError)):
        return str(error)[:1000]
    return f'Preparation failed ({type(error).__name__}). Check the server log, then retry preparation.'


def prepare_sop_version(*, user, workspace, version_id, provider=None):
    """Prepare one immutable version; retries reuse stable chunk IDs and preserve old sets."""
    _require_membership(user, workspace)
    try:
        version = SOPVersion.objects.select_related('sop').get(pk=version_id, sop__workspace=workspace)
    except SOPVersion.DoesNotExist as error:
        raise PermissionDenied('That SOP version is not in the active workspace.') from error

    provider = provider or SentenceTransformersProvider()
    model_name = provider.model_name
    dimensions = provider.dimensions
    max_chars = settings.SOP_CHUNK_MAX_CHARS
    if dimensions != 768:
        raise PreparationError(
            'The current SOP chunk vector column is 768-dimensional. Change the model dimension with a matching schema migration.'
        )
    version.processing_status = SOPVersion.ProcessingStatus.PREPARING
    version.preparation_error = ''
    version.save(update_fields=('processing_status', 'preparation_error'))

    try:
        specs = chunk_text(version.canonical_text, max_chars=max_chars)
        if not specs:
            raise PreparationError('No non-empty paragraphs were found in this SOP version.')
        vectors = _validate_vectors(
            provider.embed_documents([spec.chunk_text for spec in specs]), len(specs), dimensions,
        )
        key = _preparation_key(version, model_name, dimensions, max_chars)

        with transaction.atomic():
            locked_version = SOPVersion.objects.select_for_update().select_related('sop').get(
                pk=version.pk, sop__workspace=workspace,
            )
            if content_sha256(locked_version.canonical_text) != version.content_hash:
                raise PreparationError('Canonical SOP text no longer matches its stored content hash.')
            existing = list(SOPChunk.objects.select_for_update().filter(
                sop_version=locked_version, preparation_key=key,
            ).order_by('chunk_index'))
            if existing:
                if len(existing) != len(specs) or any(
                    chunk.chunk_index != spec.chunk_index
                    or chunk.start_char != spec.start_char
                    or chunk.end_char != spec.end_char
                    or chunk.section_heading != spec.section_heading
                    or chunk.chunk_text != spec.chunk_text
                    or chunk.content_hash != content_sha256(spec.chunk_text)
                    for chunk, spec in zip(existing, specs)
                ):
                    raise PreparationError(
                        'Stored chunks do not match this preparation key. Existing chunk references were preserved; '
                        'change the chunker version before retrying.'
                    )
                for chunk, vector in zip(existing, vectors):
                    chunk.embedding = vector
                SOPChunk.objects.bulk_update(existing, ('embedding',))
            else:
                SOPChunk.objects.bulk_create([
                    SOPChunk(
                        sop_version=locked_version,
                        preparation_key=key,
                        chunk_index=spec.chunk_index,
                        section_heading=spec.section_heading,
                        chunk_text=spec.chunk_text,
                        start_char=spec.start_char,
                        end_char=spec.end_char,
                        content_hash=content_sha256(spec.chunk_text),
                        embedding=vector,
                        embedding_model=model_name,
                        embedding_dimensions=dimensions,
                    )
                    for spec, vector in zip(specs, vectors)
                ])
            locked_version.processing_status = SOPVersion.ProcessingStatus.READY
            locked_version.preparation_error = ''
            locked_version.embedding_model = model_name
            locked_version.embedding_dimensions = dimensions
            locked_version.active_preparation_key = key
            locked_version.prepared_at = timezone.now()
            locked_version.save(update_fields=(
                'processing_status', 'preparation_error', 'embedding_model',
                'embedding_dimensions', 'active_preparation_key', 'prepared_at',
            ))
        return locked_version
    except Exception as error:
        failed = SOPVersion.objects.select_related('sop').get(pk=version.pk)
        failed.processing_status = SOPVersion.ProcessingStatus.FAILED
        failed.preparation_error = _failure_message(error)
        failed.save(update_fields=('processing_status', 'preparation_error'))
        if isinstance(error, (PreparationError, ValidationError, PermissionDenied)):
            raise
        raise PreparationError(failed.preparation_error) from error


def _cosine_similarity(left, right):
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _keyword_score(chunk_text, terms, query):
    lowered = chunk_text.casefold()
    hits = sum(lowered.count(term) for term in terms)
    if query.casefold() in lowered:
        hits += len(terms) + 1
    return hits


def _semantic_rank(chunks, query_vector, active_keys):
    active_chunks = [
        chunk for chunk in chunks
        if chunk.embedding is not None and chunk.preparation_key == active_keys.get(chunk.sop_version_id)
    ]
    if connection.vendor == 'postgresql' and active_chunks:
        conditions = []
        params = ['[' + ','.join(format(float(value), '.9g') for value in query_vector) + ']']
        for version_id, key in active_keys.items():
            conditions.append('(sop_version_id = %s AND preparation_key = %s)')
            params.extend((version_id, key))
        params.append(max(100, len(active_chunks)))
        sql = (
            'SELECT id, embedding <=> %s::vector AS distance '
            'FROM documents_sopchunk '
            'WHERE embedding IS NOT NULL AND (' + ' OR '.join(conditions) + ') '
            'ORDER BY distance ASC, id ASC LIMIT %s'
        )
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            distances = {row[0]: float(row[1]) for row in cursor.fetchall()}
        ranked = sorted(distances, key=lambda pk: (distances[pk], pk))
        return ranked
    scores = {
        chunk.pk: _cosine_similarity(chunk.embedding, query_vector)
        for chunk in active_chunks
    }
    return sorted(scores, key=lambda pk: (-scores[pk], pk))


def retrieve_evidence(*, user, workspace, selected_version_ids, query, top_k=5, neighbor_chunks=1, provider=None):
    """Retrieve evidence only from caller-selected versions in their active workspace."""
    _require_membership(user, workspace)
    query = (query or '').strip()
    if not query:
        raise ValidationError('Enter a search query.')
    try:
        version_ids = [int(value) for value in selected_version_ids]
    except (TypeError, ValueError) as error:
        raise ValidationError('Selected SOP version IDs must be integers.') from error
    if not version_ids or any(value < 1 for value in version_ids):
        raise ValidationError('Select at least one SOP version to search.')
    if len(version_ids) != len(set(version_ids)):
        raise ValidationError('Selected SOP versions must not contain duplicates.')
    versions = list(SOPVersion.objects.select_related('sop').filter(
        pk__in=version_ids, sop__workspace=workspace,
    ))
    by_id = {version.pk: version for version in versions}
    if set(by_id) != set(version_ids):
        raise PermissionDenied('One or more selected SOP versions are not in the active workspace.')
    unprepared = [version.pk for version in versions if not version.active_preparation_key]
    if unprepared:
        raise ValidationError(f'SOP version(s) {", ".join(map(str, unprepared))} are not prepared. Prepare them, then retry.')

    provider = provider or SentenceTransformersProvider()
    incompatible = [
        version.pk for version in versions
        if version.embedding_model != provider.model_name or version.embedding_dimensions != provider.dimensions
    ]
    if incompatible:
        raise ValidationError(
            f'SOP version(s) {", ".join(map(str, incompatible))} use another embedding model. Reprepare them with {provider.model_name}.'
        )

    keys = {version.pk: version.active_preparation_key for version in versions}
    active_filter = Q()
    for version_id, key in keys.items():
        active_filter |= Q(sop_version_id=version_id, preparation_key=key)
    chunks = list(SOPChunk.objects.filter(active_filter).select_related('sop_version__sop').order_by(
        'sop_version_id', 'chunk_index', 'pk',
    ))
    if not chunks:
        raise ValidationError('No chunks are available for the selected prepared SOP versions.')

    semantic_warning = ''
    semantic_ids = []
    semantic_available = True
    try:
        query_vector = _validate_vectors([provider.embed_query(query)], 1, provider.dimensions)[0]
        semantic_ids = _semantic_rank(chunks, query_vector, keys)
    except Exception as error:
        semantic_available = False
        semantic_warning = _failure_message(error)

    terms = list(dict.fromkeys(re.findall(r'[^\W_]+', query.casefold(), flags=re.UNICODE)))
    keyword_ranked = sorted(
        ((chunk.pk, _keyword_score(chunk.chunk_text, terms, query)) for chunk in chunks),
        key=lambda item: (-item[1], item[0]),
    )
    keyword_ranked = [pk for pk, score in keyword_ranked if score > 0]

    rrf = {}
    sources = {}
    for label, ranked in (('semantic', semantic_ids), ('keyword', keyword_ranked)):
        for rank, chunk_id in enumerate(ranked, start=1):
            rrf[chunk_id] = rrf.get(chunk_id, 0.0) + 1.0 / (60 + rank)
            sources.setdefault(chunk_id, set()).add(label)
    chunk_by_id = {chunk.pk: chunk for chunk in chunks}
    ranked_ids = sorted(rrf, key=lambda chunk_id: (-rrf[chunk_id], chunk_id))[:max(1, top_k)]
    chunks_by_version = {}
    for chunk in chunks:
        chunks_by_version.setdefault((chunk.sop_version_id, chunk.preparation_key), []).append(chunk)

    results = []
    for chunk_id in ranked_ids:
        chunk = chunk_by_id[chunk_id]
        same_version = chunks_by_version[(chunk.sop_version_id, chunk.preparation_key)]
        index_to_pos = {item.chunk_index: position for position, item in enumerate(same_version)}
        position = index_to_pos[chunk.chunk_index]
        left = max(0, position - max(0, neighbor_chunks))
        right = min(len(same_version), position + max(0, neighbor_chunks) + 1)
        context = same_version[left:right]
        start, end = context[0].start_char, context[-1].end_char
        source = by_id[chunk.sop_version_id].canonical_text
        results.append(RetrievedEvidence(
            chunk=chunk,
            score=rrf[chunk_id],
            matched_by=tuple(sorted(sources[chunk_id])),
            passage_text=source[start:end],
            passage_start=start,
            passage_end=end,
            context_chunk_ids=tuple(item.pk for item in context),
        ))
    return RetrievalResult(
        query=query, workspace_id=workspace.pk, selected_version_ids=tuple(version_ids),
        results=tuple(results), semantic_available=semantic_available,
        semantic_warning=semantic_warning,
    )
