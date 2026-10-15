# Synthetic data only

All records in `patients.json` are deliberately fabricated. Never add real patient
records, PHI, credentials, or exports from clinical systems.

The dataset uses a fixed reference date of `2026-10-15`. Medication statuses,
lab flags, susceptibilities, diagnoses, and infection history are supplied fixture
values, not conclusions drawn by the application.

| ID | Scenario |
| --- | --- |
| `PATIENT_001` | E. coli urine culture, previous E. coli UTI, antibiotic allergy, active cephalexin, elevated creatinine |
| `PATIENT_002` | E. coli urine culture with no recorded prior infection or prescriptions |
| `PATIENT_003` | S. aureus wound culture, severe penicillin allergy, active doxycycline |
| `PATIENT_004` | No-growth blood culture, prior Klebsiella UTI, elevated creatinine, incomplete allergy details |
| `PATIENT_005` | Sparse record with an encounter and missing optional history |

Historical microbiology and explicit previous infections are separate collections.
A positive culture alone does not establish an infection. Missing collections
default to empty lists; an empty allergy list does not assert that a patient has
no allergies. Missing scalar details remain `null` in CLI output.
