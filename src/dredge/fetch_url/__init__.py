from contextlib import contextmanager

import requests

from . import _session

__all__ = ["fetch_url", "FetchError", "NotFoundError"]


class FetchError(Exception):
    pass


class NotFoundError(FetchError):
    pass


@contextmanager
def fetch_url(url):
    try:
        response = _session.get(url)
    except requests.RequestException as e:
        raise FetchError(str(e)) from e
    try:
        if response.status_code == 404:
            raise NotFoundError(url)
        if response.status_code >= 400:
            raise FetchError(f"HTTP {response.status_code}: {url}")
        response.raw.decode_content = True
        yield response.raw
    finally:
        response.close()
