"""Small, synthetic, labeled scenarios for later assessment evaluation.

These are scenario descriptors, not regulatory criteria or source text. They must
be paired with a reviewed pack before model behavior is evaluated.
"""

EVALUATION_CASES = (
    {
        'case_id': 'omission',
        'label': 'Omission',
        'scenario': 'The selected SOP contains no provision addressing the check.',
        'expected_category': 'not_found',
    },
    {
        'case_id': 'partial_coverage',
        'label': 'Partial coverage',
        'scenario': 'The SOP describes some, but not all, elements asked by the check.',
        'expected_category': 'partially_addressed',
    },
    {
        'case_id': 'conflict',
        'label': 'Conflict',
        'scenario': 'An explicit SOP instruction appears inconsistent with the check.',
        'expected_category': 'potential_conflict',
    },
    {
        'case_id': 'valid_paraphrase',
        'label': 'Valid paraphrase',
        'scenario': 'The SOP covers the check using different wording without changing its meaning.',
        'expected_category': 'addressed',
    },
    {
        'case_id': 'supporting_document_evidence',
        'label': 'Supporting-document evidence',
        'scenario': 'The primary SOP points to a selected supporting SOP that contains the relevant procedure.',
        'expected_category': 'addressed',
    },
    {
        'case_id': 'missing_referenced_sop',
        'label': 'Missing referenced SOP',
        'scenario': 'The primary SOP relies on a referenced SOP that is not among the selected documents.',
        'expected_category': 'unable_to_determine',
    },
)
