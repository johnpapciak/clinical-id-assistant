import asyncio
import json

from agents import RunContextWrapper, function_tool

from infectious_disease_assistant.rag import service
from infectious_disease_assistant.tools.patient_tools import PatientContext


@function_tool(failure_error_function=None)
async def search_guidelines(
    context: RunContextWrapper[PatientContext], query: str, top_k: int = 3,
) -> str:
    """Search synthetic guideline review context by topic, without patient details; use only when relevant."""
    matches = await asyncio.to_thread(service.search_guidelines, query, top_k)
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
