class AnalysisError(Exception):
    pass


class AnalysisNotFoundError(AnalysisError):
    pass


class AnalysisExpiredError(AnalysisError):
    pass


class AnalysisPreviewUnavailableError(AnalysisError):
    pass


class AnalysisCollectionUnavailableError(AnalysisError):
    pass


class AnalysisCollectionConflictError(AnalysisError):
    pass


class AnalysisRetryConflictError(AnalysisError):
    pass
