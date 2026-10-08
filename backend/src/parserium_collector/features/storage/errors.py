class ArtifactStorageError(Exception):
    code = "storage_error"
    retryable = False


class ArtifactStorageUnavailableError(ArtifactStorageError):
    code = "storage_unavailable"
    retryable = True


class ArtifactIntegrityError(ArtifactStorageError):
    code = "storage_integrity_failure"


class ArtifactConfigurationError(ArtifactStorageError):
    code = "storage_configuration_error"


class ArtifactNotFoundError(ArtifactStorageError):
    code = "artifact_not_found"


class InvalidStorageKeyError(ArtifactStorageError):
    code = "invalid_storage_key"


class ScratchStorageError(Exception):
    code = "scratch_storage_error"


class ScratchCapacityError(ScratchStorageError):
    code = "scratch_capacity_error"


class ScratchContainmentError(ScratchStorageError):
    code = "scratch_containment_error"
