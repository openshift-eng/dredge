import pytest

from dredge.fetcher import _session


@pytest.fixture(autouse=True)
def reset_session():
    _session._session = None
    _session._auth_failed_domains.clear()
    yield
    _session._session = None
    _session._auth_failed_domains.clear()
