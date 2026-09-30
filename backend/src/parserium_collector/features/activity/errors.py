class ActivityError(Exception):
    pass


class ActivityNotFoundError(ActivityError):
    pass


class ActivityConflictError(ActivityError):
    pass
