from collections.abc import Generator
from contextlib import contextmanager

import requests
from urllib3.response import HTTPResponse

from . import _session

__all__ = ["FetchError", "NotFoundError", "fetch_url"]


class FetchError(Exception):
    pass


class NotFoundError(FetchError):
    pass


@contextmanager
def fetch_url(url: str) -> Generator[HTTPResponse, None, None]:
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
