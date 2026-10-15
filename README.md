# infectious-disease-assistant

An educational Python project demonstrating an agentic infectious-disease workflow
using only synthetic patient data. Never use real protected health information
(PHI). This project is for learning, not clinical use.

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

`IDA_DATA_DIR` defaults to `data`, relative to the working directory. The loader
validates record structure but cannot detect PHI or enforce synthetic provenance.
Only add deliberately fabricated data.

## Patient lookup

From the repository root with the virtual environment active:

```sh
python -m infectious_disease_assistant PATIENT_001
```

Omit the ID to enter it at a prompt. Successful lookups print a JSON summary;
invalid IDs, unknown patients, or data-loading errors print an error to stderr
and exit with status 1. IDs must match `PATIENT_` followed by exactly three ASCII
digits. For lookups from another working directory, set `IDA_DATA_DIR` to the
absolute path of this repository's `data/` directory. Fixtures live in the
repository and are not bundled into the installed Python package.

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
`PatientNotFoundError` for a valid but absent ID. File and schema failures raise
`DataLoadError` from `infectious_disease_assistant.data_loader`.

The dataset's `reference_date` anchors all retrieval; the system clock is unused.
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
example model name and leaves the key blank.

```sh
python -m infectious_disease_assistant.agent_demo PATIENT_001
python -m infectious_disease_assistant.agent_demo PATIENT_004
```

This command makes live OpenAI API calls. It validates the patient locally, then
sends only a synthetic patient ID and a new-microbiology-result notification.
The model chooses which of the five tools to call. Each tool is bound to the
selected patient through local SDK context; the model cannot select another ID.
Tool results include the patient ID, dataset reference date, and JSON records
serialized from the deterministic service models. The full patient summary used
for local validation is not sent to the model.

The instructions ask the model to inspect microbiology first and retrieve other
sections only when relevant. Compare the examples to observe tool selection:
`Tool requested:` and `Tool completed:` lines go to stderr while one final review
goes to stdout. Sequences can vary between patients and between runs; the
application does not hardcode different tool plans for different patients.

The agent must cite tool sources, distinguish missing from unreviewed data, and
avoid diagnoses, prescriptions, or recommendations to change medication. Every
successful output is prefixed with a fixed decision-support notice. Its only
tools are read-only retrieval functions, with no handoffs or action tools.
These instructions do not prove that generated text is clinically safe or free
of unsupported claims; a human must review the response against its sources.

The SDK executes model-requested functions locally, sends their JSON results
back to the model, and repeats until it returns a final response or reaches the
eight-turn limit. Tool failures abort the run. SDK tracing and request response
storage are disabled, and no session or database is used.

Tests use mocked model responses, including two different tool sequences through
the actual SDK runner. `python -m pytest` needs no API credentials and makes no
live model requests. Mocked tests verify orchestration, not live clinical output.

## Architecture and scope

The package uses a `src/` layout. `config.py` holds configuration; `models/`
defines Pydantic data contracts, `data_loader.py` reads and validates JSON, and
`services/` retrieves typed patient information. `tools/patient_tools.py` adds SDK
wrappers, `agent.py` configures one coordinator and its run loop, and
`agent_demo.py` provides the agent CLI. `data/` contains five synthetic fixtures.
Each service call loads a fresh dataset; this small project needs no cache,
repository interface, or database.

The planned workflow receives synthetic lab results through FastAPI, coordinates
controlled tools with the OpenAI Agents SDK, consults synthetic FHIR data and RAG,
and produces a structured summary for human approval before any simulated action.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagram and boundaries.

This milestone adds one coordinator agent and read-only function tools while
preserving the deterministic services and lookup CLI. FHIR integration,
databases, RAG, FastAPI, multiple agents, and a frontend are deferred.
