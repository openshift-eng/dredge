import logging
import re
import shutil
from urllib.parse import urlparse

from ..fetcher import NotFoundError, fetch_url
from ._types import ArtifactEntry, ArtifactType

logger = logging.getLogger(__name__)


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with fetch_url(url) as body, open(dest, "wb") as f:
        shutil.copyfileobj(body, f)
    logger.info(f"Downloaded to: {dest}")


def list_dir(url) -> list[ArtifactEntry]:
    try:
        with fetch_url(url) as body:
            html = body.read().decode()
    except NotFoundError:
        return []

    url_path = urlparse(url).path.rstrip("/") + "/"

    entries: list[ArtifactEntry] = []
    for row in re.finditer(r"<li[^>]*>(.*?)</li>", html, re.DOTALL):
        row_html = row.group(1)
        href_match = re.search(r'href="(/gcs/[^"]+)"', row_html)
        if not href_match:
            continue
        href = href_match.group(1)
        if not href.startswith(url_path):
            continue
        name = href.rstrip("/").split("/")[-1]
        if not name:
            continue

        if href.endswith("/"):
            entries.append(ArtifactEntry(filename=name, size=None, type=ArtifactType.DIR))
        else:
            size = None
            size_match = re.search(r'pure-u-1-5[^>]*>([^<]+)<', row_html)
            if size_match:
                size_text = size_match.group(1).strip()
                if size_text.isdigit():
                    size = int(size_text)
            entries.append(ArtifactEntry(filename=name, size=size, type=ArtifactType.FILE))

    return entries
