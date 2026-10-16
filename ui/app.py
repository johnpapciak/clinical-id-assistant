import streamlit as st
from pydantic import ValidationError
from pydantic_settings import SettingsError

from infectious_disease_assistant.models.investigation import InvestigationResponse
from infectious_disease_assistant.review_client import ReviewAPIError, ReviewClient, ReviewUISettings


def show_records(title, records):
    st.subheader(title)
    if records:
        st.dataframe([record.model_dump(mode="json") for record in records], hide_index=True)
    else:
        st.caption("No information surfaced in this summary; this does not establish absence.")


def show_investigation(record: InvestigationResponse):
    st.header("Investigation")
    st.text(f"Investigation ID: {record.investigation_id}")
    st.text(f"Synthetic patient: {record.patient_id} | Result: {record.microbiology_result_id}")
    st.info(f"Workflow status: {record.status}")
    st.caption(f"Created: {record.created_at.isoformat()} | Updated: {record.updated_at.isoformat()}")
    summary = record.summary
    if summary is None:
        st.info("No summary is available. Refresh a running investigation; failed investigations require a new run.")
        return
    st.header("Structured clinical summary")
    st.text(f"Organism: {summary.organism or 'Not supplied'}")
    st.text(f"Specimen: {summary.specimen_type or 'Not supplied'}")
    show_records("Relevant medications", summary.relevant_medications)
    show_records("Relevant allergies", summary.relevant_allergies)
    show_records("Relevant labs", summary.relevant_labs)
    show_records("Previous infections", summary.previous_infections)
    st.subheader("Important findings")
    for finding in summary.important_findings:
        st.text(finding.text)
        st.text("Evidence: " + ", ".join(f"{ref.tool_name}[{ref.record_index}]" for ref in finding.evidence))
    if not summary.important_findings:
        st.caption("No findings surfaced.")
    for title, items in [("Missing information", summary.missing_information),
                         ("Items for clinician review", summary.items_for_clinician_review)]:
        st.subheader(title)
        for item in items:
            st.text(item)
        if not items:
            st.caption("No items listed.")
    st.subheader("Evidence sources")
    st.text(", ".join(summary.evidence_sources) or "No evidence sources listed.")
    st.subheader("Guideline sources")
    if summary.guideline_sources:
        st.dataframe([source.model_dump(mode="json") for source in summary.guideline_sources], hide_index=True)
    else:
        st.caption("No guideline sources cited for this investigation. Retrieval is optional.")
    st.subheader("Proposed follow-up action")
    if summary.proposed_action:
        st.text(summary.proposed_action.title)
        st.text(summary.proposed_action.description)
        st.caption("Only explicit approval can create a simulated follow-up task.")
    else:
        st.caption("No action proposed. Approval records review only.")
    st.info(summary.disclaimer)
    if record.approval:
        st.subheader("Recorded decision")
        st.text(f"{record.approval.decision} by {record.approval.reviewer_id} at {record.approval.decided_at.isoformat()}")
        if record.approval.note:
            st.text(record.approval.note)
    if record.followup_task:
        st.success("Simulated follow-up task created")
        st.text(f"Task ID: {record.followup_task.task_id}")
        st.text(record.followup_task.title)
        st.text(record.followup_task.description)
    with st.expander("Complete structured API response"):
        st.json(record.model_dump(mode="json"))


def run(client: ReviewClient):
    st.caption("Patient access and reviewer identity come from the backend's synthetic development configuration.")
    with st.form("new_investigation"):
        st.subheader("Start an investigation")
        patient_id = st.text_input("Synthetic patient ID", value="PATIENT_001")
        result_id = st.text_input("Synthetic microbiology result ID", value="MICRO_001_RECENT")
        submitted = st.form_submit_button("Investigate")
    if submitted:
        st.session_state.pop("investigation_id", None)
        try:
            with st.spinner("Investigating the synthetic result…"):
                record = client.create(patient_id, result_id)
            st.session_state.investigation_id = str(record.investigation_id)
        except ReviewAPIError as error:
            st.error(str(error))

    with st.expander("Open an existing investigation"):
        with st.form("open_investigation"):
            identifier = st.text_input("Investigation ID")
            opened = st.form_submit_button("Open investigation")
        if opened:
            try:
                record = client.get(identifier)
                st.session_state.investigation_id = str(record.investigation_id)
            except ReviewAPIError as error:
                st.error(str(error))
        if st.button("Load pending reviews"):
            try:
                pending = client.pending()
                st.session_state.pending_reviews = {
                    str(row.investigation_id): f"{row.patient_id} | {row.microbiology_result_id} | {row.investigation_id}"
                    for row in pending
                }
                if not pending:
                    st.info("No pending reviews for the backend reviewer.")
            except ReviewAPIError as error:
                st.error(str(error))
        pending = st.session_state.get("pending_reviews", {})
        if pending:
            selected = st.selectbox("Pending investigation", list(pending), format_func=pending.get)
            if st.button("Open pending investigation"):
                st.session_state.investigation_id = selected

    identifier = st.session_state.get("investigation_id")
    if not identifier:
        return
    st.button("Refresh status")
    try:
        record = client.get(identifier)
    except ReviewAPIError as error:
        st.error(str(error))
        return
    if record.status == "awaiting_review" and record.summary is not None:
        st.caption("Review the summary and proposed action below before recording a decision.")
    show_investigation(record)
    if record.status != "awaiting_review" or record.summary is None:
        return
    with st.form(f"review_{identifier}"):
        st.subheader("Human review")
        note = st.text_area("Review note (required for rejection)", max_chars=1000)
        approve = st.form_submit_button("Approve")
        reject = st.form_submit_button("Reject")
    if approve or reject:
        try:
            client.review(identifier, approve=approve, note=note)
            st.session_state.pop("pending_reviews", None)
            st.rerun()
        except ReviewAPIError as error:
            st.error(str(error))


def main():
    st.set_page_config(page_title="Synthetic clinical review")
    st.title("Synthetic clinical review")
    st.warning("SYNTHETIC DATA ONLY — demonstration and clinician decision support for human review. "
               "Not autonomous medical advice. Never enter real patient information.")
    try:
        settings = ReviewUISettings()
    except (ValidationError, SettingsError):
        st.error("Invalid UI configuration. Check IDA_UI_API_URL and IDA_UI_TIMEOUT_SECONDS.")
        return
    with ReviewClient(settings) as client:
        run(client)


if __name__ == "__main__":
    main()
