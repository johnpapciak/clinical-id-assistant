# infectious-disease-assistant

A Python demo of an agentic infectious-disease workflow using synthetic patient
data and project-authored synthetic guideline context. No real protected health
information (PHI) is used or permitted. Output supports human review, not clinical
treatment decisions.

## Getting started

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
`http://127.0.0.1:8765/fhir`. Only loopback HTTP endpoints are accepted. Metadata
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
invalid references fail the investigation. This does not verify the meaning of
free-text findings or review questions; those still need human source checking.
The agent must not diagnose, prescribe, or recommend medication changes.

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
by your API key. The workflow is named **Synthetic infectious-disease investigation**:

- Model/response spans show model calls and their inputs and outputs.
- Function spans show tool names, JSON arguments, and returned JSON records.
  Patient-tool arguments are `{}` because patient selection comes from local SDK
  context. Guideline calls show `query` and `top_k` arguments and retrieved text,
  distances, and source metadata. Patient identity appears in trace metadata and
  patient-tool results.
- The `validated_clinical_summary` SDK span contains the final validated JSON.

This development workflow enables response storage and detailed trace payloads,
which intentionally include synthetic clinical data. Application logs contain
only event names, synthetic patient IDs, trace IDs/links, tool names, record
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
`OPENAI_MODEL` and `OPENAI_API_KEY` in your ignored `.env`. Initialize and ingest
the guideline database as described above if the agent requests RAG. Start the
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
`status: "completed"`, UTC `created_at`, and the existing structured `summary`.
A named historical result is investigated exactly; Python rejects a summary that
silently substitutes the newest result. The CLI keeps its existing newest-result
behavior, and the coordinator remains callable without importing FastAPI.

Copy the returned UUID to fetch the completed investigation without running the
agent again:

```sh
curl "http://127.0.0.1:8000/investigations/<investigation_id>"
```

Completed records are held in memory, limited to the latest 100 completions per
application instance. Restarting/reloading the process clears them; eviction also
makes GET return 404. Use a single worker for this demo. No persistent investigation
tables, task queue, polling states, or duplicate-request suppression are added.
Repeated POSTs create independent investigations and can make new API calls.

| Status | Meaning |
| --- | --- |
| 200 | Health or completed-investigation retrieval |
| 201 | Investigation completed and added to local history |
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
coordinator invocation, event/summary consistency checks, and bounded history.
Tests use FastAPI's `TestClient`, mocked dependencies, and an actual SDK/tool run
with a mocked model; no live API or database is required.

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
and produces a structured summary for clinician review. Human approval and
simulated downstream actions remain planned.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagram and boundaries.

The current implementation includes local synthetic FHIR retrieval, PostgreSQL/
pgvector RAG, one agent with structured output and SDK tracing, and a FastAPI
application boundary. Authentication, multiple agents, a frontend, and
approval/action execution remain future work.
