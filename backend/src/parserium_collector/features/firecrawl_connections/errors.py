class ConnectionNotFoundError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The Firecrawl connection is unavailable.")


class ConnectionPermissionError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Workspace owner access is required.")


class ConnectionNameConflictError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("A Firecrawl connection already uses that name.")


class ConnectionDefaultConflictError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The default Firecrawl connection could not be changed.")


class ConnectionUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The Firecrawl connection is not usable.")


class CredentialUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The Firecrawl credential is unavailable.")


class RemoteEndpointRejectedError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The remote Firecrawl endpoint is not allowed.")
