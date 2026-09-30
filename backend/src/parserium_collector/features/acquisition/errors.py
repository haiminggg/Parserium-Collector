class AcquisitionError(Exception):
    code = "acquisition_error"
    retryable = False


class UnsupportedSchemeError(AcquisitionError):
    code = "unsupported_scheme"


class BlockedDestinationError(AcquisitionError):
    code = "blocked_destination"


class DestinationResolutionError(AcquisitionError):
    code = "destination_resolution"
    retryable = True


class RedirectLimitError(AcquisitionError):
    code = "redirect_limit"


class DownloadTimeoutError(AcquisitionError):
    code = "download_timeout"
    retryable = True


class TlsFailureError(AcquisitionError):
    code = "tls_failure"


class DownloadNetworkError(AcquisitionError):
    code = "download_network"
    retryable = True


class TooLargeError(AcquisitionError):
    code = "too_large"


class HttpStatusError(AcquisitionError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class HttpRetryableError(HttpStatusError):
    code = "http_retryable"
    retryable = True


class HttpPermanentError(HttpStatusError):
    code = "http_permanent"


class InvalidResponseError(AcquisitionError):
    code = "http_permanent"


class InvalidDocumentError(AcquisitionError):
    code = "invalid_document"


class EncryptedDocumentError(InvalidDocumentError):
    pass


class StorageError(AcquisitionError):
    code = "storage_failure"


class StorageCapacityError(StorageError):
    pass


class StorageIntegrityError(StorageError):
    pass


class StorageContainmentError(StorageError):
    pass


class ExportUnavailableError(StorageError):
    code = "export_unavailable"


class CollectionJobNotFoundError(AcquisitionError):
    code = "collection_job_not_found"


class CollectionRetryConflictError(AcquisitionError):
    code = "collection_retry_conflict"


class DocumentNotFoundError(AcquisitionError):
    code = "document_not_found"


class DocumentDownloadUnavailableError(AcquisitionError):
    code = "document_download_unavailable"
