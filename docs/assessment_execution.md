# Requirement assessment execution

The assessment slice is split into a one-requirement development harness and a database-backed pack worker. Normal web requests only save, queue, poll, or review results; they do not call the language model.

## Local setup

Apply the migrations and prepare every SOP version selected for the assessment:

```sh
DATABASE_URL= .venv/bin/python manage.py migrate
DATABASE_URL= .venv/bin/python manage.py prepare_sop_versions 12 13 --user-id 7
```

Set these environment variables for live model execution. Keep the API key in the server environment or ignored `.env` file; it is not stored in the assessment configuration:

```sh
ASSESSMENT_LLM_API_KEY=...
ASSESSMENT_LLM_MODEL=gpt-4.1-mini
# Optional OpenAI-compatible chat-completions endpoint:
ASSESSMENT_LLM_ENDPOINT=https://api.openai.com/v1/chat/completions
```

Start one worker process alongside the web application:

```sh
DATABASE_URL= .venv/bin/python manage.py run_assessment_worker
```

The worker atomically claims due jobs from the database, holds a renewable five-minute lease, and resumes jobs whose leases expire. Provider requests have a bounded timeout. A run makes at most three automatic job attempts; failed requirements retain their technical error records and valid findings remain saved. A reviewer can queue a retry from the same frozen assessment. A new assessment is required for a fresh run.

For local checks, use `--once` to make the worker claim and process at most one due job attempt. `--provider deterministic` selects a clearly labeled test double; it always returns `Unable to determine` with an explanation that it made no substantive compliance judgment. This mode must not be treated as a live provider run.

The one-requirement harness persists at most one finding for the selected assessment/requirement and leaves the assessment in `Draft`:

```sh
DATABASE_URL= .venv/bin/python manage.py assess_requirement 23 81 --user-id 7
DATABASE_URL= .venv/bin/python manage.py assess_requirement 23 81 --user-id 7 --provider openai
```

The first command uses the deterministic test double. The second performs an actual provider call and reports that separately in its command output. No full-pack execution occurs through this command.

A draft containing a deterministic-provider result cannot be queued as a live full-pack run. Start a new assessment for a substantive provider execution.

## Evidence and review behavior

Finding categories use the master specification labels: Addressed in submitted text, Partially addressed, Potential conflict, Not found in assessed documents, and Unable to determine. A queued run freezes the provider/model, prompt version, retrieval limits, embedding configuration, and each selected version's prepared chunk set. The model receives the pinned requirement and retrieved selected-version chunks as JSON data, while instructions tell it to treat document and requirement text as untrusted data.

The backend accepts a quotation only if it is an exact contiguous slice of the cited chunk and selected SOP version. It resolves zero-based half-open offsets from the canonical source text. Repeated quotations require exact chunk-local before/after context that resolves one occurrence; ambiguous quotations fail schema validation and never become highlights. A missing-evidence result stores no fabricated evidence.

An initial `Not found in assessed documents` response is not accepted. The service performs a broader review against every active chunk from all selected SOP versions, verifies that the chunks cover the complete non-whitespace source, and sends the full chunk set for a second model review. This is limited to 60,000 total canonical source characters. Larger or incompletely prepared scopes are saved as `Unable to determine` with a documented limitation. References to an unselected `SOP-<id>` are recorded as unavailable document limitations and make the finding `Unable to determine` when they affect scope.

Provider/network/schema errors create requirement execution failure records, not `Unable to determine` findings. Reviewer decisions are append-only and do not modify the model response or previous reviews. A completed pack run requires every requirement to have one validated finding or an explicit human applicability exclusion.
