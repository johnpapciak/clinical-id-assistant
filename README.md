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

## Architecture and scope

The package uses a `src/` layout. `config.py` holds configuration; `models/`
defines Pydantic data contracts, `data_loader.py` reads and validates JSON, and
`services/` retrieves typed patient information. `tools/` remains reserved for
future controlled capabilities. `data/` contains five synthetic patient fixtures.
Each service call loads a fresh dataset; this small project needs no cache,
repository interface, or database.

The planned workflow receives synthetic lab results through FastAPI, coordinates
controlled tools with the OpenAI Agents SDK, consults synthetic FHIR data and RAG,
and produces a structured summary for human approval before any simulated action.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagram and boundaries.

This milestone adds synthetic fixtures, deterministic services, a lookup CLI,
and tests while preserving the original packaging and import smoke test. LLMs,
agents, FHIR integration, databases, RAG, FastAPI, and a frontend are deferred.
