"""Private file storage (Technical Plan section 2.1: local disk in development,
S3-compatible storage in production; only this class would change).

Files are saved under a RANDOM name (the storage key), never the uploader's
file name, so a user can't overwrite other files or use names like
"../../app/main.py" (path traversal).
"""

import re
import uuid
from pathlib import Path

_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, data: bytes) -> str:
        key = uuid.uuid4().hex
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def path_for(self, key: str) -> Path:
        # Only our own random keys are accepted: anything else could point outside the folder.
        if not _KEY_PATTERN.fullmatch(key):
            raise ValueError("Invalid storage key")
        return self.root / key[:2] / key  # 256 sub-folders keep folders small

    def delete(self, key: str) -> None:
        self.path_for(key).unlink(missing_ok=True)
