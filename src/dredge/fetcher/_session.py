import logging
import time
from urllib.parse import urlparse

import requests

from . import _auth

logger = logging.getLogger(__name__)

_session = None
_auth_failed_domains: set[str] = set()

_RETRIES = 3
_BACKOFF = 2
_TIMEOUT = 30


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def get(url):
    session = _get_session()
    last_exc = None
    for attempt in range(_RETRIES):
        try:
            response = session.get(url, stream=True, timeout=_TIMEOUT)
            if _auth.is_oauth_proxy_auth_required(response):
                domain = urlparse(url).netloc
                if domain not in _auth_failed_domains:
                    if _auth.authenticate_session(session, response, url):
                        response = session.get(url, stream=True, timeout=_TIMEOUT)
                    else:
                        _auth_failed_domains.add(domain)
            return response
        except requests.RequestException as e:
            last_exc = e
            if attempt < _RETRIES - 1:
                wait = _BACKOFF ** (attempt + 1)
                logger.warning(f"Request failed. Retrying in {wait}s...")
                time.sleep(wait)
    raise last_exc
