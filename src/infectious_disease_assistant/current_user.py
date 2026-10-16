from infectious_disease_assistant.config import Settings
from infectious_disease_assistant.models.workflow import CurrentUser


def get_current_user() -> CurrentUser:
    """Local demo identity only; replace this dependency with real authentication later."""
    settings = Settings()
    return CurrentUser(reviewer_id=settings.current_user_id,
                       assigned_patient_ids=settings.patient_assignments.get(settings.current_user_id, frozenset()))
