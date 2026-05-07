from dataclasses import dataclass
from enum import StrEnum


class ArtifactType(StrEnum):
    FILE = "file"
    DIR = "dir"


@dataclass
class ArtifactEntry:
    filename: str
    size: int | None
    type: ArtifactType
