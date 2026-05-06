import logging
import re
import shutil
from urllib.parse import urlparse

from ..fetcher import fetch_url, NotFoundError

logger = logging.getLogger(__name__)


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with fetch_url(url) as body:
        with open(dest, "wb") as f:
            shutil.copyfileobj(body, f)
    logger.info(f"Downloaded to: {dest}")


def list_dir(url):
    try:
        with fetch_url(url) as body:
            html = body.read().decode()
    except NotFoundError:
        return [], []

    url_path = urlparse(url).path.rstrip("/") + "/"

    hrefs = re.findall(r'href="(/gcs/[^"]+)"', html)
    subdirs = []
    files = []
    for href in hrefs:
        if not href.startswith(url_path):
            continue
        name = href.rstrip("/").split("/")[-1]
        if not name:
            continue
        if href.endswith("/"):
            subdirs.append(name)
        else:
            files.append(name)
    return subdirs, files
