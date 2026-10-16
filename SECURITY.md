# Security scope

This is a synthetic healthcare workflow demo, not a HIPAA-certified system or a
production-certified clinical application. Never use real PHI, real EHR
credentials, or patient documents. OpenAI, PostgreSQL, FastAPI, and Docker do not
automatically make an application HIPAA compliant.

## Controls illustrated

- Pydantic settings load configuration from environment variables or an ignored
  local `.env`. API keys and database URLs use secret types. `.env.example`
  contains no credentials; `.gitignore` excludes local secrets and key files.
- A server-selected synthetic user has an explicit set of assigned patient
  tokens. Unassigned patients are denied before any FHIR HTTP request. Services
  enforce authorization independently of routes; tools also check a trusted,
  immutable local principal. Model arguments cannot change the principal or patient.
- Creation, history, pending review, and approval/rejection use patient
  assignments. Pending queries filter assignments before applying limits.
  Invalid requests return 422 and access denials return a generic 403.
- Tools expose bounded operations, with no arbitrary SQL, FHIR URL, or write
  tool. SQL uses parameters. The FHIR adapter accepts only local synthetic
  endpoints, supported resource types, and local references, and rejects redirects.
  Domain outputs use synthetic business tokens rather than FHIR transport IDs.
- Application logs contain event metadata, not clinical records, tool payloads,
  exception messages, reviewer notes, or credentials. JSON formatting drops
  unrecognized payload fields. Access grants/denials, investigation creation,
  tools, failures, and review decisions emit explicit events; existing PostgreSQL
  run/tool/decision audit records remain available.
- SDK tracing keeps payloads out by default; detailed synthetic payloads require
  `IDA_TRACE_INCLUDE_PAYLOADS=true`. Provider response storage defaults to false
  (`IDA_STORE_MODEL_RESPONSES`). Neither setting guarantees provider retention
  terms. Keep the SDK's model/tool logging redaction defaults enabled; do not set
  `OPENAI_AGENTS_DONT_LOG_MODEL_DATA` or `OPENAI_AGENTS_DONT_LOG_TOOL_DATA` to false.
- HTTP errors omit raw inputs, internal stack traces, exception text, and secrets.
  Approval transactions still gate all simulated downstream actions.

## Development authorization limits

`IDA_CURRENT_USER_ID` chooses one identity for the process; this is not login,
authentication, or a caller-controlled header. `IDA_PATIENT_ASSIGNMENTS` maps
reviewer tokens to patient tokens. The default allows REVIEWER_001 only PATIENT_001;
missing reviewer assignments allow no patients. Server owners control these
settings. FastAPI's identity dependency is the replacement point for real identity
verification. Request scopes propagate into asynchronous tools and worker threads.

The fixture FHIR server and database adapters are trusted local components. They
do not enforce end-user identity for direct HTTP/SQL callers. A process that can
read fixtures, change settings, call internal storage, or override Python context
is outside this demo's authorization boundary. Do not expose local services
publicly. Audit logs are not tamper-proof. Saved evaluation reports and opted-in
traces can contain synthetic clinical data. Scripted evaluation uses a fixture-only
principal inside mock transport; live clinical evaluation uses configured assignments.

## Work required for regulated deployment

A real deployment would need a formal risk assessment and legal/compliance
review; appropriate vendor agreements and data-retention terms; verified identity,
MFA, scoped service credentials, centrally managed authorization and access review;
network isolation, TLS and encryption at rest, managed secrets and rotation;
protected audit retention, monitoring, incident response, backups and recovery;
dependency/security testing, data-governance procedures, clinical validation, and
operational approval. These require organization-specific implementation and
verification. This demo does not establish compliance or suitability for real care.
