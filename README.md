# infectious-disease-assistant

A Python demo of an agentic infectious-disease workflow using synthetic patient
data and project-authored synthetic guideline context. No real protected health
information (PHI) is used or permitted. Output supports human review, not clinical
treatment decisions.

## Getting started

### Complete local stack with Docker

Requires Docker Engine/Desktop with Compose v2. From a new checkout:

```sh
git clone https://github.com/johnpapciak/clinical-id-assistant.git
cd clinical-id-assistant
cp .env.example .env
```

Edit the ignored `.env`: set `POSTGRES_PASSWORD` to a new local password, and
set `OPENAI_API_KEY` and `OPENAI_MODEL` for agent/embedding calls. No credentials
are built into images. Then start the stack:

```sh
docker compose up --build
```

Alternatively, use `docker compose up --build -d --wait` to wait for healthy
services in the background. Compose runs the API, synthetic FHIR server,
PostgreSQL/pgvector, and a one-shot `migrate` service that initializes both
checksummed migration sets. No separate schema initialization is needed on a
fresh volume. Initialization runs safely again when the migration service runs.
Fixtures and project-authored guideline documents are bundled in the image;
the read-only FHIR service loads the same five patients every start.

The API waits for successful migrations, healthy PostgreSQL, and healthy FHIR,
then checks connectivity with bounded retries. Configure `IDA_STARTUP_ATTEMPTS`
and `IDA_STARTUP_DELAY_SECONDS` to change the startup retry budget. Inspect a
failed migration with `docker compose logs migrate`; the API does not start
after failed migrations.

Guideline embeddings require an explicit initialization command, which makes
OpenAI embedding calls and is safe to repeat:

```sh
docker compose run --rm api python -m infectious_disease_assistant.rag ingest
docker compose run --rm api python -m infectious_disease_assistant.rag search \
  "urine culture review and allergy context" --top-k 3
```

Without an API key the stack can still start for deterministic FHIR/health
checks, but agent investigations and guideline ingestion require credentials.
An empty guideline table is not a populated knowledge base. Check the stack:

```sh
docker compose ps -a
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8765/fhir/metadata
docker compose run --rm api check
docker compose exec guidelines-db psql -U ida_demo -d ida_guidelines \
  -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
```

The health endpoint is liveness; `api check` additionally checks FHIR metadata,
PostgreSQL, pgvector, and migrated tables. Run an investigation and review it:

```sh
curl -i -X POST http://127.0.0.1:8000/investigations \
  -H 'Content-Type: application/json' \
  -d '{"patient_id":"PATIENT_001","microbiology_result_id":"MICRO_001_RECENT"}'
curl http://127.0.0.1:8000/investigations/pending-review
curl -X POST "http://127.0.0.1:8000/investigations/<investigation_id>/approve" \
  -H 'Content-Type: application/json' -d '{"note":"Reviewed synthetic case."}'
```

Replace `<investigation_id>` with the UUID from the POST response. The initial
response is `awaiting_review`. An approved proposed task becomes a simulated
follow-up task; no medication or real EHR action occurs. The agent chooses when
to call RAG; ingestion does not force retrieval for every case. For an explicit
review topic through the independent CLI:

```sh
docker compose run --rm api python -m infectious_disease_assistant.agent_demo \
  PATIENT_001 --guideline-question "urine culture review and allergy context"
docker compose run --rm api python -m infectious_disease_assistant PATIENT_001
docker compose run --rm api python -m infectious_disease_assistant.evals.run --output /tmp/evaluation.json
```

One-off commands use `docker compose run`, so the entrypoint supplies the internal
database URL and Docker DNS configuration. They inherit the same patient
assignments as the API. The container uses `guidelines-db:5432` and
`synthetic-fhir:8765`; a host `DATABASE_URL` in `.env` is intentionally not passed
into containers. Passwords are URL-encoded by Python. Published ports bind only
to host loopback: API 8000, FHIR 8765, PostgreSQL 5433. Avoid running host servers
on these ports at the same time. Set `IDA_API_PORT`, `IDA_FHIR_PORT`, or
`IDA_POSTGRES_PORT` in `.env` to change a published host port, and adjust host
URLs accordingly. Internal service ports stay fixed. Changes to code or bundled fixtures require
rebuilding; there is no host source mount.

```sh
docker compose down
```

This stops/removes containers and the network while retaining the PostgreSQL
volume (including workflow history and embeddings). To rerun migrations manually:

```sh
docker compose run --rm migrate
```

Python containers run as UID 10001 with read-only filesystems, a temporary `/tmp`,
dropped capabilities, and no added privileges. `.dockerignore` uses an allowlist
to exclude `.env`, Git history, virtual environments, tests, keys, and reports.
The image pins its Python base digest and installs only hash-locked runtime
dependencies from `requirements.lock`; build tools stay in the builder stage.
Refresh the runtime lock deliberately with the command in its header and rerun
tests/build verification. A network requiring a custom CA can pass a public CA
bundle as the optional BuildKit `proxy_ca` secret; it is mounted only while pip
runs and is not copied into the image. Keep TLS verification enabled.

```sh
docker build --secret id=proxy_ca,src=/path/to/trusted-ca-bundle.pem \
  -t infectious-disease-assistant:local .
docker compose up --no-build -d --wait
```

### Run Python directly

Requires Python 3.11 or newer. From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
python -m pytest
```

On Windows, activate with `.venv\Scripts\activate`.

## Configuration

`Settings` in `infectious_disease_assistant.config` reads `IDA_` environment
variables and an optional `.env` file in the working directory. Environment
variables take precedence. See `.env.example` for defaults. Keep secrets out of
version control; local `.env` files are ignored.

`AgentSettings` additionally requires `OPENAI_MODEL` and `OPENAI_API_KEY`, read
from the environment or `.env`. These have no code defaults. The API key is a
Pydantic `SecretStr`; ordinary deterministic lookups need neither setting.

```python
from infectious_disease_assistant.config import Settings

settings = Settings()
```

`IDA_DATA_DIR` defaults to `data`. The local FHIR service loads
`fhir/bundle.json`; guideline ingestion loads `guidelines/manifest.json`. The application uses `IDA_FHIR_BASE_URL`, defaulting to
`http://127.0.0.1:8765/fhir`. Loopback HTTP endpoints are accepted by default.
Compose alone enables the exact `http://synthetic-fhir:8765/fhir` service host
through `IDA_FHIR_ALLOW_COMPOSE_HOST=true`; arbitrary remote FHIR hosts remain
rejected. Metadata
must declare synthetic-only data and FHIR R4 version `4.0.1`. These markers and
schema checks cannot detect PHI; only add deliberately fabricated fixtures.

## Start the synthetic FHIR API

From the repository root, start this read-only service in a separate terminal:

```sh
source .venv/bin/activate
python -m infectious_disease_assistant.fhir.server
```

It binds to `127.0.0.1`, loads the committed fixture Bundle, and uses Python's
standard-library HTTP server. No external FHIR server, database, or FastAPI is
needed. For a different port, use `--port 8766` and set
`IDA_FHIR_BASE_URL=http://127.0.0.1:8766/fhir` for client commands.

Explore the REST endpoints directly:

```sh
curl -H 'Accept: application/fhir+json' http://127.0.0.1:8765/fhir/metadata
curl -H 'Accept: application/fhir+json' http://127.0.0.1:8765/fhir/Patient/patient-001
curl --get http://127.0.0.1:8765/fhir/MedicationRequest \
  --data-urlencode 'patient=Patient/patient-001' --data-urlencode 'status=active'
```

Reads return individual FHIR resources. Searches return `Bundle` resources with
`type=searchset`, including empty Bundles for no matches. Missing reads return
404 `OperationOutcome`; writes return 405. The supported subset and local codes
are documented in [data/README.md](data/README.md).

## Patient lookup

With the FHIR service running and the virtual environment active, use another terminal:

```sh
python -m infectious_disease_assistant PATIENT_001
```

Omit the ID to enter it at a prompt. Successful lookups print a JSON summary;
invalid IDs, unknown patients, or FHIR errors print an error to stderr
and exit with status 1. IDs must match `PATIENT_` followed by exactly three ASCII
digits. Public synthetic IDs such as `PATIENT_001` are FHIR business identifiers;
the client resolves them through a `Patient.identifier` search. Resource IDs such
as `patient-001` follow FHIR's ID character rules. Only the FHIR service reads the
fixture files; clients need its base URL. Fixtures are not bundled into the
installed package.

The deterministic API is in `infectious_disease_assistant.services.patients`:

| Function | Return value |
| --- | --- |
| `get_patient_summary(patient_id)` | `PatientSummary` with demographics and all sections below, plus diagnoses and encounters |
| `get_medications(patient_id)` | `list[Medication]`, active prescriptions only |
| `get_allergies(patient_id)` | `list[Allergy]`, recorded medication allergies |
| `get_recent_labs(patient_id)` | `list[LabResult]`, within the inclusive 30-day lookback |
| `get_microbiology_results(patient_id)` | `list[MicrobiologyResult]`, recent and historical cultures |
| `get_previous_infections(patient_id)` | `list[PreviousInfection]`, explicitly recorded infection history |

Functions raise `InvalidPatientIdError` for malformed IDs and
`PatientNotFoundError` for a valid but absent ID. Transport, response-envelope,
and parsing failures raise subclasses of `FhirError` from
`infectious_disease_assistant.fhir.errors`.

The synthetic reference date advertised by FHIR metadata anchors retrieval; the system clock is unused.
Recent labs span `reference_date - 30 days` through `reference_date`, inclusive.
Microbiology, diagnoses, and encounters include records on or before that date;
previous infections include records strictly before it. Dated lists are returned
newest first. No infection is inferred from a positive culture, and no treatment
recommendations are generated. Missing age is `None`; missing collections become
empty lists. Empty lists mean no entries recorded, not confirmed clinical absence.

## Single-agent demo

Install the updated dependencies with `python -m pip install -e '.[dev]'`. Set
`OPENAI_MODEL` to a model supporting function tools and put your `OPENAI_API_KEY`
in the environment or your ignored local `.env`. `.env.example` provides an
example model name and leaves the key blank. Keep the local FHIR service running.

```sh
python -m infectious_disease_assistant.agent_demo PATIENT_001
python -m infectious_disease_assistant.agent_demo PATIENT_004
```

This command makes live OpenAI API calls. It validates the patient through the local FHIR API, then
sends only a synthetic patient ID and a new-microbiology-result notification.
The model chooses among five patient tools and optional `search_guidelines`.
Each patient tool is bound to the selected patient through local SDK context; the model cannot select another ID.
Tool results include the patient ID, dataset reference date, and JSON records
serialized from the deterministic service models. Preflight retrieves only
patient context, and subsequent tool calls retrieve only their resource sections.

The instructions ask the model to inspect microbiology first and retrieve other
sections only when relevant. Tool choices can vary between patients and runs;
the application does not hardcode different plans for different patients. The
SDK executes requested functions locally and returns their results to the model
until it produces a final summary or reaches the eight-turn limit.

The final result is a Pydantic `ClinicalSummary`, printed as JSON on stdout. It
includes the newest microbiology result's ID, organism and specimen, relevant
typed medication/allergy/lab/infection records, cited findings, missing context,
clinician-review items, evidence sources, used guideline citations, and a fixed
decision-support disclaimer.
Missing scalar information is `null`; missing collections are empty lists.

Python verifies patient identity, the newest culture fields, exact membership of
clinical records in the tools actually called, and finding citations using
zero-based indices into each tool's `records` array. `evidence_sources` lists all
tools called, even those returning empty lists. Unsupported structured facts or
invalid references fail the investigation.

The model returns a constrained `SummaryDraft`: selected facts, record references,
review-topic enums, and a follow-up proposal flag. Python renders `ClinicalSummary`
using fixed review-only sentences and a fixed simulated task proposal. The model
cannot supply free-text diagnoses, prescribing instructions, medication changes,
or invented narrative facts through this output schema. Retrieved guideline text
is never copied into final instructions. Missing records and records that were
not reviewed have distinct descriptions. This deliberately limits narrative
flexibility; fact selection, completeness, and clinical relevance still require
evaluation and human review. These checks apply to newly generated outputs;
existing persisted summaries are not rewritten.

## Tracing and development logs

The same command enables the SDK's standard tracing and prints JSON application
events to stderr. Keep `IDA_LOG_LEVEL=INFO` to see start, tool, and completion
events. A completed event includes `trace_id` and a clickable `trace_url`.

```sh
python -m infectious_disease_assistant.agent_demo PATIENT_001 \
  > /tmp/clinical-summary.json 2> /tmp/investigation.jsonl
cat /tmp/clinical-summary.json
cat /tmp/investigation.jsonl
```

Open the emitted link, or find the trace ID in the
[OpenAI Traces dashboard](https://platform.openai.com/traces) in the project used
by your API key. The workflow is named **Synthetic infectious-disease investigation**.
Default spans show high-level calls and timing, without clinical payloads.
To inspect detailed synthetic data, explicitly opt in before running the CLI:

```sh
export IDA_TRACE_INCLUDE_PAYLOADS=true
```

With this option enabled:

- Model/response spans show model calls and their inputs and outputs.
- Function spans show tool names, JSON arguments, and returned JSON records.
  Patient-tool arguments are `{}` because patient selection comes from local SDK
  context. Guideline calls show `query` and `top_k` arguments and retrieved text,
  distances, and source metadata. Patient identity appears in trace metadata and
  patient-tool results.
- The `validated_clinical_summary` SDK span contains the final validated JSON.

Detailed traces intentionally include synthetic clinical data. Provider response
storage defaults to false; `IDA_STORE_MODEL_RESPONSES=true` explicitly enables it
independently of trace payloads. These settings do not establish provider retention
guarantees. Application logs contain only event names, synthetic patient/reviewer
IDs, access operations, trace IDs/links, tool names, record
counts, durations, and error types. They do not contain tool arguments/results,
clinical summaries, or API keys. Failures before patient/configuration validation
have application logs but no remote trace.

The CLI calls the SDK's `flush_traces()` before exit. Export is best-effort;
check SDK export errors if a trace does not appear. Ensure the shell variable
`OPENAI_AGENTS_DISABLE_TRACING` is unset or `0`, API access is configured, and the
project supports tracing. No custom exporter or tracing backend is installed.
See the [SDK tracing documentation](https://openai.github.io/openai-agents-python/tracing/).

`python -m pytest` needs no API credentials or running database and makes no
live model, embedding, or trace requests. PostgreSQL integration tests skip unless
`IDA_TEST_DATABASE_URL` is explicitly set. Tests mock network responses while
exercising the actual SDK runner, structured-output parser, and native spans through a test-only in-memory processor.

## Guideline RAG: start PostgreSQL and ingest

Requires Docker with Compose. Three small, project-authored synthetic review
checklists are dedicated to CC0-1.0; provenance and scope are in
[data/guidelines/README.md](data/guidelines/README.md). They are demo retrieval
material, not validated clinical guidelines. No proprietary documents are used.

In your ignored `.env`, set `POSTGRES_PASSWORD` to a new local password and set
`DATABASE_URL` to `postgresql://ida_demo:<password>@127.0.0.1:5433/ida_guidelines`.
Replace `<password>` and URL-encode reserved characters. Set `OPENAI_API_KEY` for
embeddings; `OPENAI_MODEL` is needed only for agent investigations. Do not commit
these values. Compose reads `.env`; Python settings read it without shell exports.

```sh
docker compose up -d --wait guidelines-db
python -m infectious_disease_assistant.rag init
python -m infectious_disease_assistant.rag ingest
python -m infectious_disease_assistant.rag search \
  "E. coli urine culture interpretation and missing symptoms" --top-k 3
```

The database binds only to `127.0.0.1:5433` and persists in a named Docker volume.
`docker compose stop` stops it while retaining data. The initialization command
applies packaged, numbered SQL migrations with recorded checksums. Add a new
migration for later schema changes; do not edit one already applied. Initialization
needs extension/schema creation privileges and makes no OpenAI request.

Ingestion reads only the manifest's UTF-8 Markdown files, extracts headings and
body text, normalizes whitespace, chunks within sections, requests embeddings,
and stores documents, chunks, and vectors in PostgreSQL. `IDA_CHUNK_WORDS=120`
and `IDA_CHUNK_OVERLAP=20` control word windows. Re-ingestion atomically replaces
the listed documents and their chunks; unchanged inputs produce stable chunk IDs
without duplicates. Documents removed from the manifest are not automatically
deleted from the database.

`OPENAI_EMBEDDING_MODEL=text-embedding-3-small` and
`IDA_EMBEDDING_DIMENSIONS=1536` configure both ingestion and query embeddings.
Choose a model supporting the dimensions parameter. After changing the model or
dimensions, re-ingest: search excludes incompatible stored profiles. Embedding
calls use the existing environment API key and incur API usage. Ordinary patient
lookups and agent runs that never request RAG do not load RAG configuration.

`search_guidelines(query, top_k=3)` in `rag/service.py` validates a nonempty query
and `top_k` from 1 to 10, embeds the query, and runs pgvector cosine distance
(`<=>`) search. This small corpus uses exact nearest-neighbor search without an
approximate index. Each result includes text, document ID, title, source, license,
content hash, section, chunk index/ID, distance (lower is closer), and similarity
(`1 - distance`). These are ranking scores, not clinical confidence. No matches
return `[]`; configuration, embedding, and database failures abort predictably.

## Investigate with FHIR and optional guideline context

Keep the synthetic FHIR service and database running, ingest the corpus, and set
both `OPENAI_MODEL` and `OPENAI_API_KEY`. For a case with E. coli urine culture,
an active antibiotic, an allergy, and elevated creatinine:

```sh
python -m infectious_disease_assistant.agent_demo PATIENT_001 \
  --guideline-question "What urine culture and renal laboratory review context is missing?"
```

The question adds review context; tool selection remains the model's choice.
The agent starts with microbiology, may inspect medications/allergies/labs or
history, and may call `search_guidelines` when relevant. Check the SDK trace's
function spans for both patient tools and `search_guidelines`; choices can vary
between runs. Patient details should not appear in guideline query arguments.

`ClinicalSummary.guideline_sources` contains exact source metadata for guideline
chunks used in `important_findings`. Python checks those citations against actual
tool results. Guideline evidence uses stable indices into the RAG tool's cumulative
`records` list across searches. Retrieved but unused chunks need no final citation.
Guideline context cannot supply patient facts or authorize diagnosis, prescribing,
or medication changes. Free-text relevance still requires human review.

## Tests and PostgreSQL integration

```sh
python -m pytest
```

Offline tests cover extraction/chunking, ingestion, embedding validation and
failures, SQL persistence parameters, retrieval, provenance, tool formatting,
citation validation, and a mocked SDK run combining FHIR and RAG. External APIs
are mocked. Unit tests do not assess embedding relevance on real clinical tasks.

To exercise actual PostgreSQL transactions, migrations, persisted vectors,
pgvector ranking, embedding-profile filtering, and rollback, point
`IDA_TEST_DATABASE_URL` at a **separate disposable** pgvector database. For example,
create `ida_guidelines_test` in the local container:

```sh
docker compose exec guidelines-db createdb -U ida_demo ida_guidelines_test
# Set IDA_TEST_DATABASE_URL in your shell to the same connection URL,
# with database name ida_guidelines_test, then run:
python -m pytest -m integration tests/integration -q
```

Integration tests use temporary schemas and remove only their own schemas. The
test role needs schema/extension creation privileges. These tests use deterministic
test-only vectors and make no embedding/model API calls; they verify PostgreSQL
and pgvector behavior independently of embedding quality. `.env` alone does not
opt tests in: export `IDA_TEST_DATABASE_URL` explicitly.

## FastAPI application boundary

Install the updated dependencies with `python -m pip install -e '.[dev]'`.
Keep the synthetic FHIR service running in a separate terminal and set
`OPENAI_MODEL` and `OPENAI_API_KEY` in your ignored `.env`. Start PostgreSQL and initialize workflow tables before using investigation endpoints:
`python -m infectious_disease_assistant.workflow init`.
Initialize and ingest the guideline corpus as described above if the agent requests RAG. Start the
API from the repository root with the virtual environment active:

```sh
python -m uvicorn infectious_disease_assistant.api:app --host 127.0.0.1 --port 8000
```

The API has no authentication in this milestone and binds to loopback with this
command. Open `http://127.0.0.1:8000/docs` for generated request/response schemas.
`GET /health` is a liveness check; it does not call FHIR, the database, or OpenAI
and does not establish that investigation dependencies are ready.

```sh
curl http://127.0.0.1:8000/health
curl -i -X POST http://127.0.0.1:8000/investigations \
  -H 'Content-Type: application/json' \
  -d '{"patient_id":"PATIENT_001","microbiology_result_id":"MICRO_001_RECENT"}'
```

Use the existing synthetic business result ID, not the FHIR resource ID.
`MICRO_001_RECENT` belongs to `PATIENT_001`; `RESULT_001` illustrates the accepted
synthetic identifier format but is not present in the fixtures. Requests accept
only `patient_id` and `microbiology_result_id`, with extra fields rejected.
Patient IDs follow `PATIENT_` plus three digits; result IDs follow `MICRO_` plus
three digits and an uppercase alphanumeric suffix, or `RESULT_` plus three digits.
No clinical text, model settings, credentials, or arbitrary instructions are
accepted in the HTTP request.

POST validates the patient and result against FHIR before invoking the coordinator.
It waits for completion and returns **201 Created**, a `Location` header, and an
`InvestigationResponse` containing a UUID `investigation_id`, patient/result IDs,
`status: "awaiting_review"`, UTC `created_at`/`updated_at`, the structured
`summary`, `summary_id`, `agent_run`, and high-level `tool_events`.
`approval` and `followup_task` are initially null.
A named historical result is investigated exactly; Python rejects a summary that
silently substitutes the newest result. The CLI keeps its existing newest-result
behavior, and the coordinator remains callable without importing FastAPI.

Copy the returned UUID to fetch the completed investigation without running the
agent again:

```sh
curl "http://127.0.0.1:8000/investigations/<investigation_id>"
```

Investigations, run/tool audit records, summaries, review decisions, and simulated
follow-up tasks now persist in PostgreSQL across requests and app restarts. There
is no process-local history cache or eviction limit. The CLI remains independent
of the workflow database and only prints a summary/proposal; it cannot approve or
execute actions. Repeated POSTs create independent investigations. No background
job queue, automatic rerun, or request deduplication is added.

| Status | Meaning |
| --- | --- |
| 200 | Health or completed-investigation retrieval |
| 201 | Summary persisted and awaiting human review |
| 409 | Conflicting decision, or investigation not ready for review |
| 404 | Unknown patient, result not found for that patient, or absent investigation |
| 422 | Invalid JSON, invalid/missing identifiers, extra fields, or malformed investigation UUID |
| 502 | Invalid coordinator output or agent execution failure |
| 503 | Configuration, FHIR, OpenAI, or requested RAG dependency unavailable |
| 500 | Unexpected application failure |

Application errors use typed `code`, `message`, and `issues` fields. Validation
issues expose safe field locations and error codes, not rejected values or raw
request bodies. Upstream exception text and configuration secrets are not returned.
Existing SDK traces and high-level investigation logs remain enabled; API shutdown
flushes the standard trace exporter. No custom tracing system is introduced.

`api.py` owns HTTP validation, status mapping, and serialization. Its investigation
router resolves `InvestigationService` through a FastAPI dependency; a future
authentication dependency can protect that router without changing the agent or
application service. `services/investigations.py` owns patient/event validation,
coordinator invocation, event/summary consistency checks, and review orchestration.
`workflow/database.py` owns PostgreSQL transactions and durable records.
Tests use FastAPI's `TestClient`, mocked dependencies, and an actual SDK/tool run
with a mocked model; no live API or database is required.

## Persistent audit and human approval

The workflow uses the existing `DATABASE_URL` and Docker PostgreSQL service. It
has a separate numbered migration ledger from RAG; existing guideline migrations
are unchanged. Run initialization once before starting the API (re-running is safe):

```sh
docker compose up -d --wait guidelines-db
python -m infectious_disease_assistant.workflow init
```

The relational tables are `ida_investigations`, `ida_agent_runs`,
`ida_tool_events`, `ida_clinical_summaries`, `ida_approval_decisions`, and
`ida_followup_tasks`. They keep UUIDs, synthetic patient/result tokens, UTC
timestamps, configured model name, an agent configuration fingerprint, tool names,
call IDs, completion status/counts, one structured summary, the reviewer decision,
and the approved action. The fingerprint covers instructions, output schema,
allowed tools, SDK version, turn limit, and model options; the model name is stored
separately. API keys, connection strings, raw tool arguments/results, and complete
FHIR resources are not stored. The summary retains its selected clinical facts.

Run and tool starts are committed before further execution. Failed runs retain
sanitized exception class names and any completed tool events; unfinished tool
calls are marked `interrupted` when the application handles a failure. A hard
process crash can leave `running` records for inspection; no automatic recovery
worker is added. Configuration fields remain null if configuration never validates.
Detailed model/tool payloads are available through SDK traces only when explicitly
enabled, linked by `agent_run.trace_id`; the database audit is separate.

The agent may populate `summary.proposed_action` with a `create_followup_task`
proposal containing `title` and `description`. It has no execution tool. A summary
with no proposal may still be reviewed and approved without creating a task.
Only an explicit approval transaction can create the local simulated task.

```sh
curl http://127.0.0.1:8000/investigations/pending-review
curl -X POST "http://127.0.0.1:8000/investigations/<investigation_id>/approve" \
  -H 'Content-Type: application/json' -d '{"note":"Reviewed synthetic case."}'
curl -X POST "http://127.0.0.1:8000/investigations/<investigation_id>/reject" \
  -H 'Content-Type: application/json' -d '{"note":"Insufficient synthetic context."}'
```

Approve and reject are alternatives. Rejection records the reason and creates no
task. Approving with `modified_action` records decision `modified` and executes
that exact approved version; the original summary/proposal stays unchanged:

```json
{
  "note": "Refined the synthetic follow-up question.",
  "modified_action": {
    "kind": "create_followup_task",
    "title": "Clarify specimen context",
    "description": "Reviewer requests synthetic collection details."
  }
}
```

Review endpoints resolve `CurrentUser` through the separate `get_current_user`
dependency. `IDA_CURRENT_USER_ID=REVIEWER_001` is a server-configured local demo
identity, **not authentication** or proof of a human's identity. Request bodies and
headers cannot select a different reviewer. Replace this dependency with real
verified authentication in a later milestone. Assignment authorization already
applies to all investigation endpoints; no hospital authentication is implemented.

A row lock serializes decisions for an investigation. The decision, simulated
task insertion, and terminal status update commit in one transaction. Unique
constraints permit one decision/task per investigation, and a database trigger
rejects task insertion unless its fields match a recorded approved/modified action.
An identical retry by the same reviewer returns the original task and decision;
a different decision or payload returns 409. This guarantee covers the simulated
database task, not an external hospital side effect.

With FHIR/API running and OpenAI configured, this example creates an investigation,
checks pending review, and explicitly approves a follow-up task. If the agent
proposes none, the reviewer supplies the modified action shown here:

```sh
curl --fail-with-body -sS -X POST http://127.0.0.1:8000/investigations \
  -H 'Content-Type: application/json' \
  -d '{"patient_id":"PATIENT_001","microbiology_result_id":"MICRO_001_RECENT"}' \
  > /tmp/investigation.json
python -m json.tool /tmp/investigation.json
INVESTIGATION_ID=$(python -c 'import json; print(json.load(open("/tmp/investigation.json"))["investigation_id"])')
curl --fail-with-body -sS http://127.0.0.1:8000/investigations/pending-review
curl --fail-with-body -sS -X POST "http://127.0.0.1:8000/investigations/$INVESTIGATION_ID/approve" \
  -H 'Content-Type: application/json' \
  -d '{"note":"Reviewed synthetic case.","modified_action":{"kind":"create_followup_task","title":"Review synthetic microbiology","description":"Review missing symptom and specimen context."}}'
```

The first response has a summary and `awaiting_review`. The last response has
`status: "approved"`, an approval ID/reviewer/timestamp, and a `followup_task`
with its own ID, `simulated: true`, and the matching approval ID. No patient record,
clinical order, medication, or external system is changed.

Unit tests remain offline using a test-only store double. Production always uses
PostgreSQL. For real transactions and concurrent approval checks, export the
separate disposable `IDA_TEST_DATABASE_URL` described above and run:

```sh
python -m pytest -m integration tests/integration -q
```

## Development patient authorization

The API, deterministic services, and agent tools enforce synthetic patient
assignments. The default process identity is `REVIEWER_001`, assigned only
`PATIENT_001`. Configure the server or CLI environment to change assignments:

```sh
export IDA_CURRENT_USER_ID=REVIEWER_002
export IDA_PATIENT_ASSIGNMENTS='{"REVIEWER_001":["PATIENT_001"],"REVIEWER_002":["PATIENT_002","PATIENT_003"]}'
python -m infectious_disease_assistant PATIENT_002
```

Unassigned access returns 403 from the API and fails before FHIR retrieval. An
unknown reviewer has no patient access. The same assignments protect investigation
history, pending review, and approval/rejection; the pending limit applies after
filtering. Headers and request bodies cannot select an identity or supply grants.
This is a server-selected development identity, not authentication. The FastAPI
`get_current_user` dependency can later resolve a verified identity.

This demo is not HIPAA-certified. No real PHI should be used. See
[SECURITY.md](SECURITY.md) for the illustrated controls and deployment limitations.

## Safety evaluation

Run the 20-case deterministic suite without API credentials, a running FHIR
server, or PostgreSQL:

```sh
python -m infectious_disease_assistant.evals.run
python -m infectious_disease_assistant.evals.run --output eval-results/scripted.json
```

The default `scripted` mode executes the real SDK runner, tools, domain services,
FHIR parsing, and final safety checks against fixture-backed mock HTTP. Authored
model responses and guideline matches are frozen reference scripts. Passing this
mode verifies the pipeline and scorer; it does not measure a model's tool choices,
clinical reasoning, or vector retrieval quality. No model or embedding API calls
are made, and trace export is disabled for replay.

For a model evaluation, start the existing local synthetic FHIR server and
PostgreSQL, ingest guidelines as described above, and configure `OPENAI_MODEL`,
`OPENAI_API_KEY`, and the existing database/embedding settings. Then run:

```sh
python -m infectious_disease_assistant.evals.run --mode live --output eval-results/live.json
```

Live mode runs 15 clinical scenarios through the actual coordinator and its
configured FHIR/RAG tools, with normal SDK tracing. It can incur model and
embedding API usage. Five additional cases inject unsafe candidates directly at
the final-output safety boundary in both modes; they test rejection, rather than
whether a model spontaneously produces unsafe claims.
Live clinical cases honor the configured user's assignments; to evaluate all five
patients, explicitly assign that user `PATIENT_001` through `PATIENT_005`.
Scripted mode uses a fixture-only principal within its mock transport and cannot
grant access to a live endpoint.

The console reports pass counts, blocked attacks, failure categories, and median/
p95 clinical pipeline latency. JSON includes per-case checks, actual tool names
and arguments, accepted summaries, model/configuration version, dataset hashes,
and SDK request/token usage when available. Replay usage is explicitly nonbillable;
SDK usage excludes RAG embedding calls and does not estimate money. Reports are
ignored by Git. Exit codes are 0 for all checks passing, 1 for case failures, and
2 for invalid inputs or unavailable configuration. Missing live credentials
produce an unavailable report, never a simulated live pass.

Checks cover schema validity, required facts, grounding, tool selection/arguments,
retrieval expectations, citation metadata, prohibited claim literals, and the
human-review output contract. Literal checks supplement structured guardrails;
they are not the clinical safety boundary. The existing workflow integration
tests separately verify that only recorded approval can execute an action.
Model-judged evaluation is not run or mixed into deterministic scores.
Corpus provenance, expectations, and limitations are in
[data/evals/README.md](data/evals/README.md).

## Architecture and scope

The package uses a `src/` layout. `fhir/client.py` handles HTTP reads/searches;
`fhir/models.py` and `fhir/parsing.py` validate and map the supported R4 subset;
`services/` selects relevant typed domain records. `tools/patient_tools.py` keeps
the existing agent tool names. `fhir/server.py` is the only patient fixture reader,
backed by the five-patient `data/fhir/bundle.json` collection. Domain retrieval
has no agent framework dependency. Tests use `httpx.MockTransport` and require
neither a running FHIR service nor live model access.

The application boundary receives synthetic lab notifications through FastAPI
and coordinates controlled tools with the OpenAI Agents SDK, consults synthetic FHIR data and RAG,
and produces a structured summary for clinician review. Human review gates the creation of a simulated follow-up task.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagram and boundaries.

The current implementation includes local synthetic FHIR retrieval, PostgreSQL/
pgvector RAG, one agent with structured output and SDK tracing, and a FastAPI
application boundary with persistent workflow audit, human approval, structured
safety guardrails, and deterministic/live evaluation commands.
Authentication, multiple agents, a frontend, and real downstream integrations
remain future work.
