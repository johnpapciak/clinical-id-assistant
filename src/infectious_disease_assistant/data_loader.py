from pathlib import Path

from pydantic import ValidationError

from infectious_disease_assistant.config import Settings
from infectious_disease_assistant.models.patient import PatientDataset


class DataLoadError(ValueError):
    """The synthetic dataset could not be read or validated."""


def load_patient_data(path: Path | None = None) -> PatientDataset:
    data_path = path if path is not None else Settings().data_dir / "patients.json"
    try:
        return PatientDataset.model_validate_json(data_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        raise DataLoadError(f"Could not load synthetic patient data from {data_path}") from error
