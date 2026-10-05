class KBError(Exception):
    """Stable public code, never stores a provider body or source text."""

    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)
