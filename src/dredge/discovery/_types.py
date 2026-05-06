from enum import Enum


class JobFilter(Enum):
    FAILED = "failed"
    SUCCESS = "success"
    ALL = "all"
