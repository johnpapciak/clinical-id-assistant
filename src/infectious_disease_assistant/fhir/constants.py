BASE = "https://infectious-disease-assistant.example/fhir"
PATIENT_IDENTIFIER_SYSTEM = f"{BASE}/sid/patient"
REPORT_IDENTIFIER_SYSTEM = f"{BASE}/sid/microbiology"
ENCOUNTER_IDENTIFIER_SYSTEM = f"{BASE}/sid/encounter"
REFERENCE_DATE_URL = f"{BASE}/StructureDefinition/synthetic-reference-date"
SYNTHETIC_ONLY_URL = f"{BASE}/StructureDefinition/synthetic-only"
ORGANISM_URL = f"{BASE}/StructureDefinition/infection-organism"
TREATMENT_URL = f"{BASE}/StructureDefinition/previous-treatment"
CULTURE_SYSTEM = f"{BASE}/CodeSystem/culture-outcome"
OBSERVATION_SYSTEM = f"{BASE}/CodeSystem/microbiology-observation"
HISTORY_SYSTEM = f"{BASE}/CodeSystem/condition-category"
LAB_CATEGORY_SYSTEM = f"{BASE}/CodeSystem/observation-category"
INTERPRETATION_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
RESOURCE_TYPES = (
    "Patient", "MedicationRequest", "AllergyIntolerance", "Observation",
    "Condition", "Encounter", "DiagnosticReport", "Specimen",
)
