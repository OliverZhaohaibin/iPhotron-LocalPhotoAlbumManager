"""Progress and recoverable failures shared by extension installers."""

from collections.abc import Callable

ExtensionProgress = Callable[[int, int, str], None]


class ExtensionInstallError(Exception):
    """Stable error category with diagnostics safe to display or copy."""

    def __init__(self, category: str, stage: str, *, code: int | None = None, detail: str = ""):
        self.category = category
        self.stage = stage
        self.code = code
        self.detail = detail
        super().__init__(f"stage={stage}; category={category}; code={code}; {detail}")
