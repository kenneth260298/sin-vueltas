"""Storage for downloaded originals: a local folder now, Azure Blob Storage later."""

from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def exists(self, key: str) -> bool: ...

    def save(self, key: str, content: bytes) -> None: ...


class LocalStorage:
    """Stores files under a root folder (data/raw by default, ignored by Git)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()

    def save(self, key: str, content: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
