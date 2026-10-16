class WorkflowError(RuntimeError):
    pass


class WorkflowDatabaseError(WorkflowError):
    pass


class WorkflowNotFoundError(WorkflowError):
    pass


class ReviewConflictError(WorkflowError):
    pass


class ActionNotApprovedError(ReviewConflictError):
    pass
