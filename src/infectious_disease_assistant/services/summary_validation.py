from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from infectious_disease_assistant.models.clinical_summary import ClinicalSummary, ToolName
from infectious_disease_assistant.models.patient import MicrobiologyResult


class SummaryEvidenceError(ValueError):
    """A structured summary contains information unsupported by retrieved records."""


def validate_summary_evidence(
    summary: ClinicalSummary,
    patient_id: str,
    tool_results: Mapping[ToolName, Sequence[BaseModel]],
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
    latest = cultures[0] if cultures else None
    expected = (
        (latest.result_id, latest.organism, latest.specimen)
        if isinstance(latest, MicrobiologyResult)
        else (None, None, None)
    )
    observed = (summary.microbiology_result_id, summary.organism, summary.specimen_type)
    if observed != expected:
        raise SummaryEvidenceError("Microbiology fields must match the newest retrieved result")

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
    return summary
