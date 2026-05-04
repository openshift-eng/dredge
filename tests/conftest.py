import pytest

from dredge.fetch_url import _session


@pytest.fixture(autouse=True)
def reset_session():
    _session._session = None
    _session._auth_failed_domains.clear()
    yield
    _session._session = None
    _session._auth_failed_domains.clear()
