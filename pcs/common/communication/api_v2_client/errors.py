class ApiV2ClientError(Exception):
    """
    Base for all APIv2 client errors. Carries structured context; callers are
    expected to build the user-facing message from these attributes rather than
    from str(self), which is a fallback intended for logging only.
    """

    def __init__(self, endpoint: str):
        super().__init__()
        # transport.label: node address or unix socket path
        self.endpoint = endpoint

    def __str__(self) -> str:
        return self.__class__.__name__


class ApiV2RequestError(ApiV2ClientError):
    """
    The request payload is invalid (HTTP 400); retrying on another node won't
    help.
    """

    def __init__(self, endpoint: str, http_code: int, reason: str):
        super().__init__(endpoint)
        self.http_code = http_code
        self.reason = reason

    def __str__(self) -> str:
        return f"HTTP error {self.http_code}: {self.reason}"


class ApiV2CommunicationError(ApiV2ClientError):
    """
    Transport/protocol failure; retrying on another node may succeed. Also the
    concrete fallback for HTTP errors that are not classified further.
    """

    def __init__(
        self,
        endpoint: str,
        http_code: int | None = None,
        reason: str | None = None,
    ):
        super().__init__(endpoint)
        self.http_code = http_code
        self.reason = reason

    def __str__(self) -> str:
        if self.http_code is not None:
            return f"HTTP error {self.http_code}: {self.reason}"
        if self.reason is not None:
            return str(self.reason)
        return self.__class__.__name__


class ApiV2ConnectionError(ApiV2CommunicationError):
    """Could not establish a connection (daemon down, missing socket)."""

    def __str__(self) -> str:
        base = f"Unable to connect to {self.endpoint}"
        return f"{base}: {self.reason}" if self.reason else base


class ApiV2TimeoutError(ApiV2CommunicationError):
    """Request to an APIv2 endpoint timed out."""

    def __init__(self, endpoint: str, timeout: float | None = None):
        super().__init__(endpoint)
        self.timeout = timeout

    def __str__(self) -> str:
        if self.timeout is not None:
            return f"Request timed out after {self.timeout} seconds"
        return "Request timed out"


class ApiV2ProtocolError(ApiV2CommunicationError):
    """Response could not be parsed or did not match the expected schema."""

    def __init__(self, endpoint: str, detail: str | None = None):
        super().__init__(endpoint)
        self.detail = detail

    def __str__(self) -> str:
        if self.detail is not None:
            return f"Unexpected response: {self.detail}"
        return "Unexpected response"
