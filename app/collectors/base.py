from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Collection:
    entries: list[dict] = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    not_modified: bool = False


class Collector(Protocol):
    def collect(self, url: str, headers: dict) -> Collection: ...
