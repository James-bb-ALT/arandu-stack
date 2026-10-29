class LexicalError(RuntimeError):
    """A correction cannot satisfy the configured policy."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def to_dict(self):
        return {"code": self.code, "message": str(self)}
