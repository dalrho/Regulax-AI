"""One-requirement assessment, quote verification, and immutable persistence."""

import json
import re
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.documents.models import SOP, SOPChunk, SOPVersion
from apps.documents.preparation import retrieve_evidence
from apps.regulations.models import Requirement
from apps.workspaces.models import Membership

from .models import (
    Assessment,
    AssessmentDocument,
    AssessmentRunJob,
    Finding,
    FindingEvidence,
    RequirementExecution,
    RequirementExecutionFailure,
    RequirementExclusion,
)
from .providers import DeterministicProvider, FINDING_CATEGORIES, ProviderError


MAX_BROADER_REVIEW_CHARS = 60_000
MAX_PROVIDER_RETRIES = 1


class FindingAssessmentError(RuntimeError):
    """Technical or schema failure; never a substantive finding category."""


class OutputSchemaError(FindingAssessmentError):
    pass


class StaleWorkerError(FindingAssessmentError):
    pass


class RequirementExcluded(FindingAssessmentError):
    pass


@dataclass(frozen=True)
class ResolvedQuote:
    chunk: SOPChunk
    version: SOPVersion
    start_char: int
    end_char: int
    quote: str


def _model_payload(raw):
    if isinstance(raw, str):
        raw_text = raw
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise OutputSchemaError('Provider response is not valid JSON.') from error
    elif isinstance(raw, dict):
        payload = raw
        raw_text = json.dumps(raw, ensure_ascii=False, separators=(',', ':'))
    else:
        raise OutputSchemaError('Provider response must be a JSON object.')
    return payload, raw_text


def _validate_output(raw):
    payload, raw_text = _model_payload(raw)
    required = {'category', 'explanation', 'citations', 'limitations'}
    if not isinstance(payload, dict) or set(payload) != required:
        raise OutputSchemaError('Provider response must contain exactly category, explanation, citations, and limitations.')
    if payload['category'] not in FINDING_CATEGORIES:
        raise OutputSchemaError('Provider returned an unknown finding category.')
    if not isinstance(payload['explanation'], str) or not payload['explanation'].strip() or len(payload['explanation']) > 10000:
        raise OutputSchemaError('Provider explanation must be non-empty text.')
    if not isinstance(payload['citations'], list) or not isinstance(payload['limitations'], list):
        raise OutputSchemaError('Provider citations and limitations must be lists.')
    if len(payload['citations']) > 20 or len(payload['limitations']) > 20:
        raise OutputSchemaError('Provider returned too many citations or limitations.')
    citation_keys = {'chunk_id', 'version_id', 'quote', 'context_before', 'context_after'}
    limitation_keys = citation_keys | {'document_name'}
    for citation in payload['citations']:
        if not isinstance(citation, dict) or set(citation) != citation_keys:
            raise OutputSchemaError('Each citation must contain a chunk, selected version, exact quote, and contexts.')
    for limitation in payload['limitations']:
        if not isinstance(limitation, dict) or set(limitation) != limitation_keys:
            raise OutputSchemaError('Each limitation must contain a chunk, selected version, referenced document, exact quote, and contexts.')
        if (
            not isinstance(limitation['document_name'], str)
            or not limitation['document_name'].strip()
            or len(limitation['document_name']) > 250
        ):
            raise OutputSchemaError('Referenced document names must be non-empty text.')
    for item in [*payload['citations'], *payload['limitations']]:
        if any(not isinstance(item[key], int) or isinstance(item[key], bool) or item[key] < 1 for key in ('chunk_id', 'version_id')):
            raise OutputSchemaError('Citation chunk and version references must be positive integers.')
        if any(not isinstance(item[key], str) for key in ('quote', 'context_before', 'context_after')):
            raise OutputSchemaError('Citation quotes and contexts must be text.')
    if payload['category'] in ('addressed', 'partially_addressed', 'potential_conflict') and not payload['citations']:
        raise OutputSchemaError('A supported finding must include at least one source citation.')
    if payload['category'] == 'not_found' and payload['citations']:
        raise OutputSchemaError('A not-found finding cannot contain positive evidence citations.')
    return payload, raw_text


def _candidate_from_chunk(chunk):
    version = chunk.sop_version
    return {
        'chunk_id': chunk.pk,
        'version_id': version.pk,
        'sop_id': version.sop_id,
        'document_name': version.sop.title,
        'version_label': version.version_label,
        'start_char': chunk.start_char,
        'end_char': chunk.end_char,
        'text': chunk.chunk_text,
        '_chunk': chunk,
        '_version': version,
    }


def _provider_evidence(candidates):
    return [{key: item[key] for key in (
        'chunk_id', 'version_id', 'sop_id', 'document_name', 'version_label',
        'start_char', 'end_char', 'text',
    )} for item in candidates]


def _resolve_quote(item, candidates_by_id):
    chunk = candidates_by_id.get(item['chunk_id'])
    if chunk is None:
        raise OutputSchemaError(f"Provider cited an unselected or unretrieved chunk {item['chunk_id']}.")
    version = chunk['_version']
    if item['version_id'] != version.pk:
        raise OutputSchemaError('Provider cited a selected-version reference that does not match its chunk.')
    quote = item['quote']
    before = item['context_before']
    after = item['context_after']
    source_chunk_text = chunk['text']
    if not quote or not quote.strip() or len(quote) > 4000 or len(before) > 120 or len(after) > 120:
        raise OutputSchemaError('Citation quote or context is empty, padded, or over its size limit.')

    matches = []
    cursor = 0
    while True:
        offset = source_chunk_text.find(quote, cursor)
        if offset < 0:
            break
        end_offset = offset + len(quote)
        before_start = max(0, offset - len(before))
        after_end = min(len(source_chunk_text), end_offset + len(after))
        context_matches = (
            source_chunk_text[before_start:offset] == before
            and source_chunk_text[end_offset:after_end] == after
            and (not before or offset >= len(before))
            and (not after or end_offset + len(after) <= len(source_chunk_text))
        )
        if context_matches:
            matches.append(offset)
        cursor = offset + 1
    if not matches:
        raise OutputSchemaError(f'Quotation is not an exact source slice in chunk {chunk["chunk_id"]}.')
    if len(matches) > 1:
        raise OutputSchemaError(
            f'Quotation occurs more than once in chunk {chunk["chunk_id"]}; exact contexts did not resolve the ambiguity.'
        )
    start_char = chunk['start_char'] + matches[0]
    end_char = start_char + len(quote)
    if version.canonical_text[start_char:end_char] != quote:
        raise OutputSchemaError('Resolved quotation does not match the selected SOP version text.')
    return ResolvedQuote(
        chunk=chunk['_chunk'], version=version,
        start_char=start_char, end_char=end_char, quote=quote,
    )


def _selected_scope(assessment):
    selected = list(assessment.documents.select_related('sop_version__sop').order_by('role', 'pk'))
    if not selected or not any(item.role == AssessmentDocument.Role.PRIMARY for item in selected):
        raise FindingAssessmentError('Assessment has no pinned primary SOP version.')
    versions = [item.sop_version for item in selected]
    not_ready = [version for version in versions if version.processing_status != SOPVersion.ProcessingStatus.READY or not version.active_preparation_key]
    if not_ready:
        raise FindingAssessmentError(
            'Every selected SOP version must be prepared and ready before a requirement can be assessed: '
            + ', '.join(f'{version.sop.title} {version.version_label}' for version in not_ready)
        )
    preparation_keys = {
        version.pk: item.preparation_key or version.active_preparation_key
        for item, version in zip(selected, versions)
    }
    return versions, preparation_keys


def _retrieval_actor(assessment):
    if Membership.objects.filter(user=assessment.created_by, workspace=assessment.workspace).exists():
        return assessment.created_by
    member = Membership.objects.filter(workspace=assessment.workspace).select_related('user').order_by('pk').first()
    if member is None:
        raise FindingAssessmentError('No active workspace member is available to authorize document retrieval.')
    return member.user


def _retrieval_candidates(
    *, user, assessment, requirement, versions, preparation_keys, retrieval_provider,
    top_k=10, neighbor_chunks=0,
):
    result = retrieve_evidence(
        user=user,
        workspace=assessment.workspace,
        selected_version_ids=[version.pk for version in versions],
        query=' '.join(part for part in (
            requirement.code, requirement.title, requirement.original_text, requirement.assessment_question,
        ) if part),
        top_k=top_k,
        neighbor_chunks=neighbor_chunks,
        provider=retrieval_provider,
        selected_preparation_keys=preparation_keys,
    )
    selected_keys = preparation_keys
    chunk_ids = []
    for item in result.results:
        if item.chunk.pk not in chunk_ids:
            chunk_ids.append(item.chunk.pk)
    chunks = SOPChunk.objects.filter(pk__in=chunk_ids).select_related('sop_version__sop')
    candidates = [
        _candidate_from_chunk(chunk) for chunk in chunks
        if chunk.preparation_key == selected_keys.get(chunk.sop_version_id)
        and chunk.sop_version_id in selected_keys
    ]
    candidates.sort(key=lambda item: chunk_ids.index(item['chunk_id']))
    return result, candidates


def _comprehensive_candidates(versions, preparation_keys, *, max_chars=MAX_BROADER_REVIEW_CHARS):
    total_chars = sum(len(version.canonical_text) for version in versions)
    if total_chars > max_chars:
        return None, total_chars
    active = {}
    for version in versions:
        chunks = list(SOPChunk.objects.filter(
            sop_version=version, preparation_key=preparation_keys[version.pk],
        ).select_related('sop_version__sop').order_by('chunk_index'))
        if not chunks or ''.join(chunk.chunk_text for chunk in chunks) == '':
            return None, total_chars
        active[version.pk] = chunks
    if any(not chunks for chunks in active.values()):
        return None, total_chars
    all_candidates = [_candidate_from_chunk(chunk) for chunks in active.values() for chunk in chunks]
    # Verify complete source coverage. Chunk boundaries omit only paragraph whitespace.
    for version in versions:
        chunks = active[version.pk]
        covered = [False] * len(version.canonical_text)
        for chunk in chunks:
            for index in range(chunk.start_char, chunk.end_char):
                covered[index] = True
        if any(not covered[index] for index, char in enumerate(version.canonical_text) if not char.isspace()):
            return None, total_chars
    return all_candidates, total_chars


def _missing_sop_references(versions):
    selected_sop_ids = {version.sop_id for version in versions}
    missing = set()
    workspace_id = versions[0].sop.workspace_id
    for version in versions:
        for raw_id in re.findall(r'\bSOP\s*[-#:]?\s*0*(\d+)\b', version.canonical_text, flags=re.IGNORECASE):
            sop_id = int(raw_id)
            if sop_id not in selected_sop_ids:
                missing.add(f'SOP-{sop_id:03d}')
        source = version.canonical_text.casefold()
        for sop in SOP.objects.filter(workspace_id=workspace_id).exclude(pk__in=selected_sop_ids):
            title = sop.title.casefold().strip()
            if not title:
                continue
            cursor = 0
            while True:
                position = source.find(title, cursor)
                if position < 0:
                    break
                preceding = source[max(0, position - 100):position]
                if re.search(r'\b(?:refer(?:red)? to|see|consult|follow|in accordance with)\b', preceding):
                    missing.add(f'SOP-{sop.pk:03d} {sop.title}')
                    break
                cursor = position + len(title)
    return sorted(missing)


def _detected_reference_quotes(versions, preparation_keys):
    selected_sop_ids = {version.sop_id for version in versions}
    resolved = []
    for version in versions:
        source = version.canonical_text
        for match in re.finditer(r'\bSOP\s*[-#:]?\s*0*(\d+)\b', source, flags=re.IGNORECASE):
            if int(match.group(1)) in selected_sop_ids:
                continue
            chunk = SOPChunk.objects.filter(
                sop_version=version,
                preparation_key=preparation_keys[version.pk],
                start_char__lte=match.start(),
                end_char__gte=match.end(),
            ).select_related('sop_version__sop').first()
            if chunk and source[match.start():match.end()] == chunk.chunk_text[
                match.start() - chunk.start_char:match.end() - chunk.start_char
            ]:
                resolved.append(ResolvedQuote(
                    chunk=chunk, version=version,
                    start_char=match.start(), end_char=match.end(), quote=match.group(0),
                ))
    return resolved


def _is_referenced_doc_selected(document_name, versions):
    name = document_name.casefold().strip()
    return any(
        name == version.sop.title.casefold()
        or name == f'SOP-{version.sop_id:03d}'.casefold()
        or name == f'SOP-{version.sop_id}'.casefold()
        for version in versions
    )


def _validate_and_resolve(payload, candidates, versions):
    by_id = {candidate['chunk_id']: candidate for candidate in candidates}
    selected_version_ids = {version.pk for version in versions}
    citations = [_resolve_quote(item, by_id) for item in payload['citations']]
    limitations = [_resolve_quote(item, by_id) for item in payload['limitations']]
    if any(item.version.pk not in selected_version_ids for item in (*citations, *limitations)):
        raise OutputSchemaError('Provider referenced a version outside the frozen assessment scope.')
    identities = [(item.chunk.pk, item.start_char, item.end_char) for item in citations]
    if len(identities) != len(set(identities)):
        raise OutputSchemaError('Provider returned a duplicate citation.')
    return citations, limitations


def _assert_live_lease(assessment, job, lease_token):
    if job is None:
        return
    locked_job = AssessmentRunJob.objects.select_for_update().filter(
        pk=job.pk,
        assessment=assessment,
        status=AssessmentRunJob.Status.RUNNING,
        lease_token=lease_token,
    ).first()
    if locked_job is None or locked_job.lease_expires_at is None or locked_job.lease_expires_at <= timezone.now():
        raise StaleWorkerError('This worker no longer owns the active execution lease.')


@transaction.atomic
def _set_running(assessment, requirement, *, job=None, lease_token=None):
    if job is not None:
        _assert_live_lease(assessment, job, lease_token)
    else:
        locked_assessment = Assessment.objects.select_for_update().get(pk=assessment.pk)
        if locked_assessment.status != Assessment.Status.DRAFT:
            raise FindingAssessmentError('The one-requirement harness can only assess a draft; use the run worker for a frozen job.')
        if not locked_assessment.scope_frozen_at:
            for selected_document in locked_assessment.documents.select_related('sop_version'):
                if not selected_document.preparation_key:
                    selected_document.preparation_key = selected_document.sop_version.active_preparation_key
                    selected_document.save(update_fields=('preparation_key',))
            locked_assessment.scope_frozen_at = timezone.now()
            locked_assessment.save(update_fields=('scope_frozen_at', 'updated_at'))
        assessment.scope_frozen_at = locked_assessment.scope_frozen_at
    execution, _created = RequirementExecution.objects.select_for_update().get_or_create(
        assessment=assessment, requirement=requirement,
    )
    if RequirementExclusion.objects.filter(assessment=assessment, requirement=requirement).exists():
        execution.status = RequirementExecution.Status.EXCLUDED
        execution.save(update_fields=('status', 'updated_at'))
        raise RequirementExcluded('Requirement was explicitly excluded by a reviewer.')
    if Finding.objects.filter(assessment=assessment, requirement=requirement).exists():
        execution.status = RequirementExecution.Status.COMPLETED
        execution.save(update_fields=('status', 'updated_at'))
        return False
    execution.status = RequirementExecution.Status.RUNNING
    execution.attempt_count += 1
    execution.error_summary = ''
    execution.save(update_fields=('status', 'attempt_count', 'error_summary', 'updated_at'))
    return True


@transaction.atomic
def _record_failure(assessment, requirement, error, *, job=None, lease_token=None):
    if job is not None:
        _assert_live_lease(assessment, job, lease_token)
    execution, _created = RequirementExecution.objects.select_for_update().get_or_create(
        assessment=assessment, requirement=requirement,
    )
    if RequirementExclusion.objects.filter(assessment=assessment, requirement=requirement).exists():
        execution.status = RequirementExecution.Status.EXCLUDED
        execution.error_summary = ''
        execution.save(update_fields=('status', 'error_summary', 'updated_at'))
        return
    RequirementExecutionFailure.objects.create(
        assessment=assessment,
        requirement=requirement,
        attempt_number=max(execution.attempt_count, 1),
        error_type=type(error).__name__,
        message=str(error)[:4000],
    )
    execution.status = RequirementExecution.Status.FAILED
    execution.error_summary = str(error)[:4000]
    execution.save(update_fields=('status', 'error_summary', 'updated_at'))


@transaction.atomic
def _persist_finding(
    *, assessment, requirement, payload, raw_output, resolved_citations, resolved_limitations,
    provider, review_scope, scope_summary, scope_limitations, job=None, lease_token=None,
):
    if job is not None:
        _assert_live_lease(assessment, job, lease_token)
    execution, _created = RequirementExecution.objects.select_for_update().get_or_create(
        assessment=assessment, requirement=requirement,
    )
    if RequirementExclusion.objects.filter(assessment=assessment, requirement=requirement).exists():
        execution.status = RequirementExecution.Status.EXCLUDED
        execution.save(update_fields=('status', 'updated_at'))
        raise RequirementExcluded('Requirement was explicitly excluded by a reviewer.')
    existing = Finding.objects.filter(assessment=assessment, requirement=requirement).first()
    if existing:
        execution.status = RequirementExecution.Status.COMPLETED
        execution.save(update_fields=('status', 'updated_at'))
        return existing
    category = payload['category']
    explanation = payload['explanation'].strip()
    if scope_limitations:
        category = Finding.Category.UNABLE_TO_DETERMINE
        explanation += '\n\nEvidence limitation: ' + '; '.join(scope_limitations)
    finding = Finding.objects.create(
        assessment=assessment,
        requirement=requirement,
        category=category,
        explanation=explanation,
        model_output=payload,
        raw_model_output=raw_output,
        provider_name=provider.name,
        model_name=provider.model_name,
        review_scope=review_scope,
        scope_summary=scope_summary,
        scope_limitations=scope_limitations,
    )
    for quote in resolved_citations:
        FindingEvidence.objects.create(
            finding=finding, chunk=quote.chunk, sop_version=quote.version,
            kind=FindingEvidence.Kind.SUPPORTING,
            start_char=quote.start_char, end_char=quote.end_char, quote=quote.quote,
        )
    for quote in resolved_limitations:
        FindingEvidence.objects.create(
            finding=finding, chunk=quote.chunk, sop_version=quote.version,
            kind=FindingEvidence.Kind.LIMITATION,
            start_char=quote.start_char, end_char=quote.end_char, quote=quote.quote,
        )
    execution.status = RequirementExecution.Status.COMPLETED
    execution.error_summary = ''
    execution.save(update_fields=('status', 'error_summary', 'updated_at'))
    return finding


def _backend_scope_finding(*, assessment, requirement, provider, versions, total_chars, job, lease_token):
    message = (
        f'Broader review was not adequate: the selected SOP versions contain {total_chars:,} characters '
        f'and exceed the documented {MAX_BROADER_REVIEW_CHARS:,}-character comprehensive review limit, '
        'or their active prepared chunks do not cover the full source. No not-found result was accepted.'
    )
    payload = {
        'category': Finding.Category.UNABLE_TO_DETERMINE,
        'explanation': message,
        'citations': [],
        'limitations': [],
    }
    return _persist_finding(
        assessment=assessment, requirement=requirement, payload=payload,
        raw_output='', resolved_citations=[], resolved_limitations=[],
        provider=type('ScopeGuardProvider', (), {'name': 'backend_scope_guard', 'model_name': ''})(),
        review_scope='unavailable', scope_summary=message,
        scope_limitations=[message], job=job, lease_token=lease_token,
    )


def assess_requirement(*, assessment, requirement, provider=None, retrieval_provider=None, job=None, lease_token=None):
    """Produce one persisted finding; technical failures are recorded separately."""
    provider = provider or DeterministicProvider()
    try:
        requirement = Requirement.objects.get(
            pk=requirement.pk, pack_version_id=assessment.pack_version_id,
        )
    except Requirement.DoesNotExist as error:
        raise PermissionDenied('Requirement is not in the assessment’s pinned pack version.') from error
    should_run = _set_running(assessment, requirement, job=job, lease_token=lease_token)
    if not should_run:
        return Finding.objects.get(assessment=assessment, requirement=requirement)
    try:
        versions, preparation_keys = _selected_scope(assessment)
        run_config = assessment.execution_config if job is not None else {}
        retrieval_config = run_config.get('retrieval', {})
        retrieval, focused_candidates = _retrieval_candidates(
            user=_retrieval_actor(assessment), assessment=assessment, requirement=requirement,
            versions=versions, preparation_keys=preparation_keys,
            retrieval_provider=retrieval_provider,
            top_k=max(1, min(50, int(retrieval_config.get('top_k', 10)))),
            neighbor_chunks=max(0, min(3, int(retrieval_config.get('neighbor_chunks', 0)))),
        )
        provider_candidates = focused_candidates
        review_scope = 'focused'
        scope_summary = (
            f'Focused retrieval reviewed {len(focused_candidates)} active chunk(s) from '
            f'{len(versions)} selected SOP version(s); retrieval method availability: '
            f"{'semantic and keyword' if retrieval.semantic_available else 'keyword only'}."
        )
        raw_output = None
        payload = None
        resolved_citations = resolved_limitations = []
        last_error = None
        retry_limit = max(0, min(2, int(run_config.get('provider_validation_retries', MAX_PROVIDER_RETRIES))))
        for attempt in range(retry_limit + 1):
            try:
                payload, raw_output = _validate_output(provider.generate_finding(
                    requirement={
                        'code': requirement.code,
                        'title': requirement.title,
                        'citation': requirement.citation,
                        'original_text': requirement.original_text,
                        'assessment_question': requirement.assessment_question,
                        'applicability_notes': requirement.applicability_notes,
                        'assessment_limits': requirement.assessment_limits,
                    },
                    evidence=_provider_evidence(provider_candidates),
                    review_scope=review_scope,
                ))
                resolved_citations, resolved_limitations = _validate_and_resolve(
                    payload, provider_candidates, versions,
                )
                last_error = None
                break
            except (ProviderError, OutputSchemaError) as error:
                last_error = error
        if last_error is not None:
            raise last_error

        if payload['category'] == Finding.Category.NOT_FOUND:
            broader_limit = max(1, int(retrieval_config.get('broader_review_max_chars', MAX_BROADER_REVIEW_CHARS)))
            provider_candidates, total_chars = _comprehensive_candidates(
                versions, preparation_keys, max_chars=broader_limit,
            )
            if provider_candidates is None:
                return _backend_scope_finding(
                    assessment=assessment, requirement=requirement, provider=provider,
                    versions=versions, total_chars=total_chars, job=job, lease_token=lease_token,
                )
            review_scope = 'comprehensive'
            scope_summary = (
                f'Broader review examined all {len(provider_candidates)} active prepared chunks covering '
                f'all {len(versions)} selected SOP version(s), {total_chars:,} source characters in total. '
                'A not-found result is accepted only after this full selected-version review.'
            )
            last_error = None
            for attempt in range(retry_limit + 1):
                try:
                    payload, raw_output = _validate_output(provider.generate_finding(
                        requirement={
                            'code': requirement.code,
                            'title': requirement.title,
                            'citation': requirement.citation,
                            'original_text': requirement.original_text,
                            'assessment_question': requirement.assessment_question,
                            'applicability_notes': requirement.applicability_notes,
                            'assessment_limits': requirement.assessment_limits,
                        },
                        evidence=_provider_evidence(provider_candidates),
                        review_scope=review_scope,
                    ))
                    resolved_citations, resolved_limitations = _validate_and_resolve(
                        payload, provider_candidates, versions,
                    )
                    last_error = None
                    break
                except (ProviderError, OutputSchemaError) as error:
                    last_error = error
            if last_error is not None:
                raise last_error

        limitations = _missing_sop_references(versions)
        detected_quotes = _detected_reference_quotes(versions, preparation_keys)
        known_locations = {(quote.chunk.pk, quote.start_char, quote.end_char) for quote in resolved_limitations}
        resolved_limitations.extend(
            quote for quote in detected_quotes
            if (quote.chunk.pk, quote.start_char, quote.end_char) not in known_locations
        )
        for limitation, resolved in zip(payload['limitations'], resolved_limitations):
            if not _is_referenced_doc_selected(limitation['document_name'], versions):
                limitations.append(
                    f"Selected evidence references unavailable document {limitation['document_name']!r} "
                    f"in SOP-{resolved.version.sop_id:03d} {resolved.version.version_label}."
                )
        limitations = sorted(set(limitations))
        if limitations:
            scope_summary += ' An unavailable referenced SOP was detected; the finding is limited accordingly.'
        return _persist_finding(
            assessment=assessment,
            requirement=requirement,
            payload=payload,
            raw_output=raw_output,
            resolved_citations=resolved_citations,
            resolved_limitations=resolved_limitations,
            provider=provider,
            review_scope=review_scope,
            scope_summary=scope_summary,
            scope_limitations=limitations,
            job=job,
            lease_token=lease_token,
        )
    except RequirementExcluded:
        raise
    except StaleWorkerError:
        raise
    except Exception as error:
        _record_failure(assessment, requirement, error, job=job, lease_token=lease_token)
        if isinstance(error, FindingAssessmentError):
            raise
        raise FindingAssessmentError(str(error)) from error
