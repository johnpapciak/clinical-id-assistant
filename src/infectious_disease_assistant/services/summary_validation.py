from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from infectious_disease_assistant.models.clinical_summary import ClinicalSummary, ToolName
from infectious_disease_assistant.models.patient import MicrobiologyResult
from infectious_disease_assistant.rag.models import GuidelineMatch


class SummaryEvidenceError(ValueError):
    """A structured summary contains information unsupported by retrieved records."""


def validate_summary_evidence(
    summary: ClinicalSummary,
    patient_id: str,
    tool_results: Mapping[ToolName, Sequence[BaseModel]],
    *,
    microbiology_result_id: str | None = None,
) -> ClinicalSummary:
    if summary.patient_id != patient_id:
        raise SummaryEvidenceError("Summary patient does not match the investigation")
    if "get_microbiology_results" not in tool_results:
        raise SummaryEvidenceError("Microbiology must be retrieved before summarizing")
    if len(summary.evidence_sources) != len(set(summary.evidence_sources)):
        raise SummaryEvidenceError("Evidence sources must be unique")
    if set(summary.evidence_sources) != set(tool_results):
        raise SummaryEvidenceError("Evidence sources must match the tools actually called")

    cultures = tool_results["get_microbiology_results"]
    if microbiology_result_id is None:
        selected = cultures[0] if cultures else None
    else:
        matches = [item for item in cultures if isinstance(item, MicrobiologyResult) and item.result_id == microbiology_result_id]
        if len(matches) != 1:
            raise SummaryEvidenceError("The requested microbiology result was not uniquely retrieved")
        selected = matches[0]
    expected = (
        (selected.result_id, selected.organism, selected.specimen)
        if isinstance(selected, MicrobiologyResult)
        else (None, None, None)
    )
    observed = (summary.microbiology_result_id, summary.organism, summary.specimen_type)
    if observed != expected:
        target = "newest retrieved result" if microbiology_result_id is None else "requested microbiology result"
        raise SummaryEvidenceError(f"Microbiology fields must match the {target}")

    sections: list[tuple[ToolName, Sequence[BaseModel]]] = [
        ("get_medications", summary.relevant_medications),
        ("get_allergies", summary.relevant_allergies),
        ("get_recent_labs", summary.relevant_labs),
        ("get_previous_infections", summary.previous_infections),
    ]
    for tool_name, records in sections:
        retrieved = tool_results.get(tool_name, ())
        if any(record not in retrieved for record in records):
            raise SummaryEvidenceError(f"Summary includes unsupported records from {tool_name}")
    for finding in summary.important_findings:
        for reference in finding.evidence:
            if reference.record_index >= len(tool_results.get(reference.tool_name, ())):
                raise SummaryEvidenceError("A finding cites a record that was not retrieved")
    retrieved_guidelines = tool_results.get("search_guidelines", ())
    used_indices = {
        reference.record_index
        for finding in summary.important_findings
        for reference in finding.evidence
        if reference.tool_name == "search_guidelines"
    }
    expected_citations = [
        record.citation() for index, record in enumerate(retrieved_guidelines)
        if index in used_indices and isinstance(record, GuidelineMatch)
    ]
    if (
        len(summary.guideline_sources) != len(expected_citations)
        or any(citation not in expected_citations for citation in summary.guideline_sources)
        or len({citation.chunk_id for citation in summary.guideline_sources}) != len(summary.guideline_sources)
    ):
        raise SummaryEvidenceError("Guideline citations must match the retrieved chunks used in findings")
    return summary
