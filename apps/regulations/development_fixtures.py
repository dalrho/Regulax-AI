"""Unapproved development fixture for the initial U.S. CGMP pack.

Source snapshots were checked against the eCFR on 2026-11-24. These proposed
checks are intentionally not an approved assessment pack: a qualified reviewer
must confirm applicability, decomposition, wording, and source snapshot before
the fixture can be replaced by a publishable version.
"""

SOURCE_URL_211_100 = 'https://www.ecfr.gov/current/title-21/chapter-I/subchapter-C/part-211/subpart-F/section-211.100'
SOURCE_URL_211_192 = 'https://www.ecfr.gov/current/title-21/chapter-I/subchapter-C/part-211/subpart-J/section-211.192'

DEVELOPMENT_PACK = {
    'slug': 'us-fda-cgmp-deviation-investigation-development',
    'title': 'U.S. FDA CGMP — Production Procedures and Investigations (Development Fixture)',
    'authority': 'U.S. Food and Drug Administration',
    'jurisdiction': 'United States',
    'product_category': 'Finished pharmaceuticals',
    'description': 'Unapproved development fixture for selected provisions of 21 CFR 211.100 and 211.192.',
    'scope': 'Development scenarios for written production and process control procedures, deviations, and production record investigations. Not an approved regulatory assessment pack.',
    'source_snapshot_date': '2026-11-24',
    'source_name': 'Electronic Code of Federal Regulations, Title 21, Part 211',
}

_TEXT_211_100_A = (
    'There shall be written procedures for production and process control designed to assure that the drug products '
    'have the identity, strength, quality, and purity they purport or are represented to possess. Such procedures '
    'shall include all requirements in this subpart. These written procedures, including any changes, shall be '
    'drafted, reviewed, and approved by the appropriate organizational units and reviewed and approved by the '
    'quality control unit.'
)
_TEXT_211_100_B = (
    'Written production and process control procedures shall be followed in the execution of the various production '
    'and process control functions and shall be documented at the time of performance. Any deviation from the '
    'written procedures shall be recorded and justified.'
)
_TEXT_211_192 = (
    'All drug product production and control records, including those for packaging and labeling, shall be reviewed '
    'and approved by the quality control unit to determine compliance with all established, approved written '
    'procedures before a batch is released or distributed. Any unexplained discrepancy (including a percentage of '
    'theoretical yield exceeding the maximum or minimum percentages established in master production and control '
    'records) or the failure of a batch or any of its components to meet any of its specifications shall be thoroughly '
    'investigated, whether or not the batch has already been distributed. The investigation shall extend to other '
    'batches of the same drug product and other drug products that may have been associated with the specific '
    'failure or discrepancy. A written record of the investigation shall be made and shall include the conclusions '
    'and followup.'
)

_LIMITS = (
    'SOP text alone cannot establish that the procedure is implemented, records are complete, or a batch decision '
    'was correct. Review the applicable procedures and their linked records; do not infer actual practice from '
    'document wording.'
)

DEVELOPMENT_REQUIREMENTS = (
    {
        'code': '211.100(a)-purpose', 'citation': '21 CFR 211.100(a)',
        'title': 'Purpose of written production and process control procedures',
        'original_text': _TEXT_211_100_A,
        'assessment_question': 'Does the SOP set out written production and process control procedures designed to assure the stated identity, strength, quality, and purity of the drug products?',
        'applicability_notes': 'Selected provision; applicability to the product and operation requires reviewer confirmation.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.100(a)-subpart', 'citation': '21 CFR 211.100(a)',
        'title': 'Coverage of applicable subpart requirements',
        'original_text': _TEXT_211_100_A,
        'assessment_question': 'Does the procedure set address the relevant requirements of the applicable subpart, as determined by a qualified reviewer?',
        'applicability_notes': 'The complete subpart is broader than this selected pack and is not enumerated here.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.100(a)-draft-review-approval', 'citation': '21 CFR 211.100(a)',
        'title': 'Procedure drafting, review, and approval',
        'original_text': _TEXT_211_100_A,
        'assessment_question': 'Does the SOP describe drafting, review, and approval by appropriate organizational units, including review and approval by the quality control unit?',
        'applicability_notes': 'Approval roles and terminology may vary by site; verify against the governing quality system.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.100(b)-follow-procedures', 'citation': '21 CFR 211.100(b)',
        'title': 'Following written production procedures',
        'original_text': _TEXT_211_100_B,
        'assessment_question': 'Does the SOP explain that written procedures are to be followed while production and process control functions are performed?',
        'applicability_notes': 'The SOP describes the process; implementation must be checked in records and practice.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.100(b)-contemporaneous-documentation', 'citation': '21 CFR 211.100(b)',
        'title': 'Documentation at time of performance',
        'original_text': _TEXT_211_100_B,
        'assessment_question': 'Does the SOP require production and process control functions to be documented at the time they are performed?',
        'applicability_notes': 'Review the procedure and associated records where available.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.100(b)-deviation-record', 'citation': '21 CFR 211.100(b)',
        'title': 'Recording procedure deviations',
        'original_text': _TEXT_211_100_B,
        'assessment_question': 'Does the SOP require deviations from written procedures to be recorded?',
        'applicability_notes': 'This check addresses documentation of a deviation, not its investigation workflow.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.100(b)-deviation-justification', 'citation': '21 CFR 211.100(b)',
        'title': 'Justifying procedure deviations',
        'original_text': _TEXT_211_100_B,
        'assessment_question': 'Does the SOP require deviations from written procedures to be justified?',
        'applicability_notes': 'The expected form and approver for a justification require reviewer confirmation.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.192-record-review', 'citation': '21 CFR 211.192',
        'title': 'Production and control record review',
        'original_text': _TEXT_211_192,
        'assessment_question': 'Does the SOP require review and approval of drug product production and control records, including packaging and labeling records, by the quality control unit?',
        'applicability_notes': 'Selected provision; record types and site procedures require reviewer confirmation.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.192-review-compliance', 'citation': '21 CFR 211.192',
        'title': 'Review against approved procedures',
        'original_text': _TEXT_211_192,
        'assessment_question': 'Does the record review determine compliance with established, approved written procedures?',
        'applicability_notes': 'A procedure should define how review findings are documented and escalated.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.192-review-before-release', 'citation': '21 CFR 211.192',
        'title': 'Record review before batch release or distribution',
        'original_text': _TEXT_211_192,
        'assessment_question': 'Does the SOP place the required record review before a batch is released or distributed?',
        'applicability_notes': 'The operational release control requires corroborating records to assess implementation.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.192-investigate-discrepancies', 'citation': '21 CFR 211.192',
        'title': 'Investigation of unexplained discrepancies',
        'original_text': _TEXT_211_192,
        'assessment_question': 'Does the SOP require thorough investigation of unexplained discrepancies, including specified yield deviations?',
        'applicability_notes': 'The example yield limits are established in master production and control records.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.192-investigate-specification-failure', 'citation': '21 CFR 211.192',
        'title': 'Investigation of specification failures',
        'original_text': _TEXT_211_192,
        'assessment_question': 'Does the SOP require thorough investigation when a batch or any component fails to meet a specification?',
        'applicability_notes': 'The investigation is required whether or not the batch has already been distributed.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.192-investigate-after-distribution', 'citation': '21 CFR 211.192',
        'title': 'Investigation regardless of distribution',
        'original_text': _TEXT_211_192,
        'assessment_question': 'Does the SOP make clear that an investigation is required even when the batch has already been distributed?',
        'applicability_notes': 'This is part of the investigation trigger and should be checked with the full deviation process.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.192-extend-same-product-batches', 'citation': '21 CFR 211.192',
        'title': 'Investigation scope across same-product batches',
        'original_text': _TEXT_211_192,
        'assessment_question': 'Does the SOP require investigation scope to extend to other batches of the same drug product?',
        'applicability_notes': 'Assess the described scope and decision process; do not infer investigation adequacy from the SOP alone.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.192-extend-associated-products', 'citation': '21 CFR 211.192',
        'title': 'Investigation scope across associated products',
        'original_text': _TEXT_211_192,
        'assessment_question': 'Does the SOP address extending the investigation to other drug products that may be associated with the failure or discrepancy?',
        'applicability_notes': 'The association decision is case-specific and requires human review.',
        'assessment_limits': _LIMITS,
    },
    {
        'code': '211.192-written-investigation-record', 'citation': '21 CFR 211.192',
        'title': 'Written investigation record and conclusions',
        'original_text': _TEXT_211_192,
        'assessment_question': 'Does the SOP require a written investigation record that includes conclusions and followup?',
        'applicability_notes': 'This combines the written record with its stated required contents.',
        'assessment_limits': _LIMITS,
    },
)
