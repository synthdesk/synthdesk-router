"""
Spine SDK error types.
"""


class SpineError(Exception):
    """Base exception for spine SDK errors."""

    pass


class SpineCorruptionError(SpineError):
    """Raised when event spine file is corrupted or malformed."""

    def __init__(self, line_number: int, message: str = ""):
        self.line_number = line_number
        super().__init__(
            f"Spine corruption at line {line_number}: {message}"
            if message
            else f"Spine corruption at line {line_number}"
        )


class UnsupportedSchemaVersion(SpineError):
    """Raised when encountering unknown schema version."""

    def __init__(self, version: str):
        self.version = version
        super().__init__(f"Unsupported schema version: {version}")


class PayloadValidationError(SpineError):
    """Raised when payload does not match expected schema."""

    pass
