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

`IDA_DATA_DIR` defaults to `data`, relative to the working directory. It is a
location setting only: configuration does not inspect data or enforce synthetic
provenance. Only add deliberately fabricated data.

## Architecture and scope

The package uses a `src/` layout. `config.py` holds configuration; `models/` will
hold Pydantic data contracts, `services/` application workflows, and `tools/`
controlled capabilities. `data/` is reserved for synthetic fixtures.

The planned workflow receives synthetic lab results through FastAPI, coordinates
controlled tools with the OpenAI Agents SDK, consults synthetic FHIR data and RAG,
and produces a structured summary for human approval before any simulated action.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagram and boundaries.

This milestone provides packaging, configuration, documentation, and an import
smoke test. LLMs, agents, FHIR integration, databases, RAG, FastAPI, and a frontend
are deferred.
