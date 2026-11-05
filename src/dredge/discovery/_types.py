from enum import Enum


class JobFilter(Enum):
    FAILED = "failed"
    SUCCESS = "success"
    ALL = "all"


class GitHubTransport(Enum):
    HTTP = "http"
    GH = "gh"


class GitHubDiscoveryError(Exception):
    """Expected GitHub transport or response failure, safe to display to the user."""
