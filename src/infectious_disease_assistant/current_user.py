from infectious_disease_assistant.config import Settings
from infectious_disease_assistant.models.workflow import CurrentUser


def get_current_user() -> CurrentUser:
    """Local demo identity only; replace this dependency with real authentication later."""
    return CurrentUser(reviewer_id=Settings().current_user_id)
