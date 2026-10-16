CREATE TABLE ida_investigations (
    investigation_id uuid PRIMARY KEY,
    patient_id text NOT NULL CHECK (patient_id ~ '^PATIENT_[0-9]{3}$'),
    microbiology_result_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'awaiting_review', 'approved', 'rejected', 'failed')),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE INDEX ida_pending_reviews ON ida_investigations (created_at, investigation_id) WHERE status = 'awaiting_review';

CREATE TABLE ida_agent_runs (
    agent_run_id uuid PRIMARY KEY,
    investigation_id uuid NOT NULL UNIQUE REFERENCES ida_investigations,
    trace_id text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    model_name text,
    configuration_version text,
    error_type text
);
CREATE TABLE ida_tool_events (
    tool_event_id uuid PRIMARY KEY,
    agent_run_id uuid NOT NULL REFERENCES ida_agent_runs,
    tool_call_id text NOT NULL,
    tool_name text NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'completed', 'interrupted')),
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    record_count integer CHECK (record_count >= 0),
    UNIQUE (agent_run_id, tool_call_id)
);
CREATE TABLE ida_clinical_summaries (
    summary_id uuid PRIMARY KEY,
    investigation_id uuid NOT NULL UNIQUE REFERENCES ida_investigations,
    agent_run_id uuid NOT NULL UNIQUE REFERENCES ida_agent_runs,
    content jsonb NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE TABLE ida_approval_decisions (
    approval_id uuid PRIMARY KEY,
    investigation_id uuid NOT NULL UNIQUE REFERENCES ida_investigations,
    summary_id uuid NOT NULL REFERENCES ida_clinical_summaries,
    reviewer_id text NOT NULL CHECK (reviewer_id ~ '^REVIEWER_[0-9]{3}$'),
    decision text NOT NULL CHECK (decision IN ('approved', 'modified', 'rejected')),
    note text,
    approved_action jsonb,
    decided_at timestamptz NOT NULL,
    CHECK (decision <> 'rejected' OR approved_action IS NULL)
);
CREATE TABLE ida_followup_tasks (
    task_id uuid PRIMARY KEY,
    investigation_id uuid NOT NULL UNIQUE REFERENCES ida_investigations,
    approval_id uuid NOT NULL UNIQUE REFERENCES ida_approval_decisions,
    patient_id text NOT NULL,
    title text NOT NULL,
    description text NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE FUNCTION ida_check_followup_approval() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ida_approval_decisions a
        JOIN ida_investigations i USING (investigation_id)
        WHERE a.approval_id = NEW.approval_id AND a.investigation_id = NEW.investigation_id
          AND a.decision IN ('approved', 'modified') AND a.approved_action IS NOT NULL
          AND a.approved_action->>'kind' = 'create_followup_task'
          AND a.approved_action->>'title' = NEW.title
          AND a.approved_action->>'description' = NEW.description
          AND i.patient_id = NEW.patient_id
    ) THEN
        RAISE EXCEPTION 'Approved follow-up action required';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER ida_followup_requires_approval BEFORE INSERT OR UPDATE ON ida_followup_tasks
FOR EACH ROW EXECUTE FUNCTION ida_check_followup_approval();
