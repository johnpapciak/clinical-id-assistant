import asyncio
import json
from typing import Annotated

from pydantic import Field, StringConstraints
from infectious_disease_assistant.rag.models import GuidelineSearch

from agents import RunContextWrapper, function_tool

from infectious_disease_assistant.rag import service
from infectious_disease_assistant.tools.patient_tools import PatientContext


@function_tool(failure_error_function=None)
async def search_guidelines(
    context: RunContextWrapper[PatientContext],
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)],
    top_k: Annotated[int, Field(ge=1, le=10, strict=True)] = 3,
) -> str:
    """Search synthetic guideline review context by topic, without patient details; use only when relevant."""
    request = GuidelineSearch(query=query, top_k=top_k)
    if 'PATIENT_' in request.query.upper():
        raise ValueError('Guideline queries must use topics without patient identifiers')
    matches = await asyncio.to_thread(service.search_guidelines, request.query, request.top_k)
    records = context.context.tool_results.setdefault("search_guidelines", [])
    known_ids = {record.chunk_id for record in records}
    for match in matches:
        if match.chunk_id not in known_ids:
            records.append(match)
            known_ids.add(match.chunk_id)
    return json.dumps({
        "query": query.strip(),
        "matched_chunk_ids": [match.chunk_id for match in matches],
        "records": [record.model_dump(mode="json") for record in records],
        "notice": "Cumulative records with stable zero-based indices. Synthetic guideline context, not patient facts or treatment advice.",
    })
