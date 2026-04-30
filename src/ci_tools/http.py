import logging
import re
import time

import requests

logger = logging.getLogger(__name__)


def fetch_page(url, retries=3, backoff=2):
    """HTTP GET with retries and exponential backoff."""
    for attempt in range(retries):
        try:
            logger.info(f"Fetching: {url}")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            if attempt < retries - 1:
                wait_time = backoff ** (attempt + 1)
                logger.warning(f"Request failed: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                logger.error(f"Request failed after {retries} attempts: {e}")
                raise
    return None


def download_artifact(url, dest, retries=3, backoff=2):
    """Stream download artifact to destination. Returns False on 404."""
    for attempt in range(retries):
        try:
            logger.info(f"Downloading: {url}")
            response = requests.get(url, stream=True, timeout=60)
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
        except requests.RequestException as e:
            if attempt < retries - 1:
                wait_time = backoff ** (attempt + 1)
                logger.warning(f"Download failed: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                logger.warning(f"Download failed after {retries} attempts: {e}")
                return False
    return False


def list_gcsweb_dir(url):
    """List entries from a gcsweb directory listing.
    Returns (subdirs, files) tuple of name lists, or ([], []) on error/404.
    """
    try:
        response = requests.get(url, timeout=30)
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
