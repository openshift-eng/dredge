import logging
import re
import time
from urllib.parse import urlparse

import requests

from . import auth

logger = logging.getLogger(__name__)

_session = None
_auth_failed_domains = set()


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def session_get(url, **kwargs):
    session = _get_session()
    response = session.get(url, **kwargs)

    if auth.is_oauth_proxy_auth_required(response):
        domain = urlparse(url).netloc
        if domain not in _auth_failed_domains:
            if auth.authenticate_session(session, response, url):
                response = session.get(url, **kwargs)
            else:
                _auth_failed_domains.add(domain)

    return response


def _request_with_retry(request_fn, retries=3, backoff=2):
    """Call request_fn() with retry and exponential backoff. Returns response."""
    for attempt in range(retries):
        try:
            return request_fn()
        except requests.RequestException:
            if attempt < retries - 1:
                wait_time = backoff ** (attempt + 1)
                logger.warning(f"Request failed. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise


def fetch_page(url, retries=3, backoff=2):
    """HTTP GET with retries and exponential backoff."""
    logger.info(f"Fetching: {url}")
    response = _request_with_retry(
        lambda: session_get(url, timeout=30), retries, backoff
    )
    response.raise_for_status()
    return response.text


def download_artifact(url, dest, retries=3, backoff=2):
    """Stream download artifact to destination. Returns False on 404."""
    logger.info(f"Downloading: {url}")
    try:
        response = _request_with_retry(
            lambda: session_get(url, stream=True, timeout=60), retries, backoff
        )
    except requests.RequestException as e:
        logger.warning(f"Download failed after {retries} attempts: {e}")
        return False

    if response.status_code == 404:
        logger.info(f"Artifact not found (404): {url}")
        return False
    response.raise_for_status()

    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    logger.info(f"Downloaded to: {dest}")
    return True


def list_gcsweb_dir(url):
    """List entries from a gcsweb directory listing.
    Returns (subdirs, files) tuple of name lists, or ([], []) on error/404.
    """
    try:
        response = session_get(url, timeout=30)
        if response.status_code == 404:
            return [], []
        response.raise_for_status()
        html = response.text
    except requests.RequestException:
        return [], []

    all_hrefs = re.findall(r'href="([^"]+)"', html)

    subdirs = []
    files = []
    for href in all_hrefs:
        name = href.rstrip("/").split("/")[-1]
        if not name or name == "..":
            continue
        if href.endswith("/"):
            subdirs.append(name)
        else:
            files.append(name)

    return subdirs, files
