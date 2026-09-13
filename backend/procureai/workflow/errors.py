class WorkflowError(Exception):
    """Unknown run or illegal transition. `code` is stable for API mapping."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
