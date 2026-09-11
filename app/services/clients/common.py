from enum import Enum, auto

class UpdateStatus(Enum):
    SKIPPED = auto()
    SUCCESS = auto()
    FAILED = auto()