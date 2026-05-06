# OAuth Proxy Auth Chain (verified against qe-private-deck-ci, 2026-05-04):
#
#  1. GET prow-deck/view/gs/...        → 403 + _oauth_proxy cookie
#     SCRAPE: <form action="/oauth/start"> with hidden "rd" field
#  2. GET prow-deck/oauth/start?rd=... → 302 → oauth-openshift/oauth/authorize
#  3. GET oauth-openshift/oauth/authorize
#     → 200, SCRAPE: single <a href="...?idp=RedHat_Internal_SSO"> link
#  4. GET oauth-openshift/oauth/authorize?idp=...
#     → 302 → idp.ci.openshift.org/auth
#  5. GET idp.ci.openshift.org/auth    → 302 → /auth/sso
#  6. GET idp.ci.openshift.org/auth/sso → 302 → auth.redhat.com/auth/realms/...
#  7. GET auth.redhat.com/...          → 401 + www-authenticate: Negotiate
#     KERBEROS: SPNEGO token via gssapi
#  8. GET auth.redhat.com/... + Negotiate token → 302 → idp/callback
#  9. GET idp/callback                 → 303 → idp/approval
# 10. GET idp/approval                 → 303 → oauth-openshift/oauth2callback
# 11. GET oauth-openshift/oauth2callback
#     → 302 → oauth-openshift/oauth/authorize (revisit with session cookie)
# 12. GET oauth-openshift/oauth/authorize (with ssn cookie)
#     → 302 → prow-deck/oauth/callback?code=sha256~...
# 13. GET prow-deck/oauth/callback     → 302 + Set-Cookie: _oauth_proxy=<token>
# 14. Retry original URL with cookie   → 200
#
# Steps 1 and 3 require HTML scraping. Step 11 revisits /oauth/authorize with a
# session cookie — loop detection allows this (different cookies = different key).

import base64
import json
import logging
import re
from html import unescape as html_unescape
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

import requests

try:
    import gssapi
    _HAS_KERBEROS = True
except ImportError:
    _HAS_KERBEROS = False

logger = logging.getLogger(__name__)

_MAX_AUTH_HOPS = 20
_KERBEROS_DOMAIN = "auth.redhat.com"
_DEFAULT_TRUSTED_DOMAINS = [".openshiftapps.com", ".openshift.org", ".redhat.com"]
_COOKIE_CACHE_DIR = Path.home() / ".config" / "dredge" / "cookies"

_trusted_domains = list(_DEFAULT_TRUSTED_DOMAINS)
_auth_failed_domains: set[str] = set()


class AuthenticationError(Exception):
    pass


def configure(extra_trusted_domains=None):
    global _trusted_domains
    _trusted_domains = list(_DEFAULT_TRUSTED_DOMAINS)
    if extra_trusted_domains:
        _trusted_domains.extend(extra_trusted_domains)


def is_oauth_proxy_auth_required(response):
    if response.status_code != 403:
        return False
    set_cookie = response.headers.get("Set-Cookie", "")
    return "_oauth_proxy=" in set_cookie


def _is_trusted_domain(hostname):
    for domain in _trusted_domains:
        if domain.startswith("."):
            if hostname.endswith(domain) or hostname == domain[1:]:
                return True
        else:
            if hostname == domain:
                return True
    return False


def _check_trusted_redirect(url):
    hostname = urlparse(url).hostname
    if not _is_trusted_domain(hostname):
        raise AuthenticationError(
            f"Redirect to untrusted domain: {hostname} "
            f"(trusted: {', '.join(_trusted_domains)}). "
            f"Use --trusted-redirect-domain to add it."
        )


def _extract_form_redirect(response):
    html = response.text

    form_match = re.search(
        r"<form[^>]*\bmethod=[\"'](\w+)[\"'][^>]*\baction=[\"']([^\"']+)[\"']",
        html, re.I,
    )
    if not form_match:
        form_match = re.search(
            r"<form[^>]*\baction=[\"']([^\"']+)[\"'][^>]*\bmethod=[\"'](\w+)[\"']",
            html, re.I,
        )
        if not form_match:
            return None
        action, method = form_match.group(1), form_match.group(2)
    else:
        method, action = form_match.group(1), form_match.group(2)

    method = method.upper()
    action = html_unescape(action)
    url = urljoin(response.url, action)

    inputs = re.findall(r"<input[^>]+>", html, re.I)
    form_data = {}
    for inp in inputs:
        if not re.search(r"type=[\"']hidden[\"']", inp, re.I):
            continue
        name_m = re.search(r"name=[\"']([^\"']+)[\"']", inp, re.I)
        val_m = re.search(r"value=[\"']([^\"']*)[\"']", inp, re.I)
        if name_m:
            form_data[name_m.group(1)] = html_unescape(val_m.group(1)) if val_m else ""

    if method == "GET":
        if form_data:
            url = f"{url}?{urlencode(form_data)}"
        return ("GET", url, None)
    else:
        return ("POST", url, form_data or None)


def _extract_link_redirect(response):
    html = response.text
    hrefs = re.findall(r"href=[\"']([^\"']+)[\"']", html)
    links = [
        h for h in hrefs
        if not h.startswith(("data:", "#", "javascript:"))
    ]
    if len(links) == 1:
        url = urljoin(response.url, html_unescape(links[0]))
        return ("GET", url, None)
    return None


def _extract_scraped_redirect(response):
    result = _extract_form_redirect(response)
    if result:
        return result
    return _extract_link_redirect(response)


def _generate_kerberos_token(hostname):
    if not _HAS_KERBEROS:
        return None
    try:
        name = gssapi.Name(f"HTTP@{hostname}", gssapi.NameType.hostbased_service)
        ctx = gssapi.SecurityContext(name=name, usage="initiate")
        token = ctx.step()
        return base64.b64encode(token).decode()
    except gssapi.exceptions.GSSError as e:
        logger.warning(f"Kerberos authentication failed: {e}")
        return None


def _follow_auth_chain(start_url):
    session = requests.Session()
    visited = set()
    method = "GET"
    url = start_url
    data = None

    for hop in range(_MAX_AUTH_HOPS):
        domain_cookies = frozenset(
            (c.name, c.value) for c in session.cookies
            if c.domain and urlparse(url).hostname.endswith(c.domain.lstrip("."))
        )
        key = (method, url, domain_cookies)
        if key in visited:
            raise AuthenticationError(f"Redirect loop detected at {url}")
        visited.add(key)

        logger.debug(f"Auth chain hop {hop + 1}: {method} {url}")
        response = session.request(
            method, url, data=data, allow_redirects=False, timeout=30,
        )

        if response.is_redirect:
            url = urljoin(url, response.headers["Location"])
            _check_trusted_redirect(url)
            method = "GET"
            data = None
            continue

        if response.status_code == 401:
            www_auth = response.headers.get("www-authenticate", "")
            if "Negotiate" in www_auth:
                hostname = urlparse(url).hostname
                if hostname != _KERBEROS_DOMAIN:
                    raise AuthenticationError(
                        f"Kerberos requested by {hostname}, "
                        f"but only {_KERBEROS_DOMAIN} is allowed"
                    )
                token = _generate_kerberos_token(hostname)
                if not token:
                    if not _HAS_KERBEROS:
                        raise AuthenticationError(
                            "Kerberos authentication required but gssapi is not installed. "
                            "Install with: uv sync --extra kerberos"
                        )
                    raise AuthenticationError(
                        "No valid Kerberos ticket. Run 'kinit' and retry."
                    )
                response = session.request(
                    method, url, data=data,
                    headers={"Authorization": f"Negotiate {token}"},
                    allow_redirects=False, timeout=30,
                )
                if response.is_redirect:
                    url = urljoin(url, response.headers["Location"])
                    _check_trusted_redirect(url)
                    method = "GET"
                    data = None
                    continue
                raise AuthenticationError(
                    f"Kerberos rejected by {hostname} (status {response.status_code})"
                )
            raise AuthenticationError(
                f"Unsupported authentication method at {url}: {www_auth}"
            )

        redirect = _extract_scraped_redirect(response)
        if redirect:
            method, url, data = redirect
            _check_trusted_redirect(url)
            continue

        for cookie in session.cookies:
            if cookie.name == "_oauth_proxy":
                return session

        raise AuthenticationError(
            f"Auth chain stuck at {url} (status {response.status_code})"
        )

    raise AuthenticationError(f"Too many redirects ({_MAX_AUTH_HOPS})")


def _load_cached_cookies(domain):
    path = _COOKIE_CACHE_DIR / f"{domain}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_cookies(domain, cookies):
    _COOKIE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _COOKIE_CACHE_DIR / f"{domain}.json"
    path.write_text(json.dumps(cookies))
    logger.info(f"Cached authentication cookies for {domain}")


def _clear_cached_cookies(domain):
    path = _COOKIE_CACHE_DIR / f"{domain}.json"
    if path.exists():
        path.unlink()


def authenticate_session(session, failed_response, url):
    domain = urlparse(url).netloc

    cached = _load_cached_cookies(domain)
    if cached:
        for name, value in cached.items():
            session.cookies.set(name, value, domain=domain)
        test = session.get(url, timeout=30, allow_redirects=False)
        if not is_oauth_proxy_auth_required(test):
            logger.info(f"Using cached authentication for {domain}")
            return True
        logger.info(f"Cached cookies expired for {domain}")
        _clear_cached_cookies(domain)
        for name in cached:
            try:
                session.cookies.clear(domain=domain, path="/", name=name)
            except KeyError:
                pass

    redirect = _extract_scraped_redirect(failed_response)
    if not redirect:
        logger.error(f"Could not find login form in 403 response from {domain}")
        return False

    _method, start_url, _data = redirect
    try:
        auth_session = _follow_auth_chain(start_url)
    except AuthenticationError as e:
        logger.error(f"Authentication failed for {domain}: {e}")
        return False

    cookies = {}
    for cookie in auth_session.cookies:
        if cookie.domain and domain in cookie.domain:
            cookies[cookie.name] = cookie.value
            session.cookies.set(
                cookie.name, cookie.value,
                domain=cookie.domain, path=cookie.path,
            )

    if "_oauth_proxy" not in cookies:
        logger.error(f"Auth completed but no session cookie received from {domain}")
        return False

    _save_cookies(domain, cookies)
    logger.info(f"Authentication successful for {domain}")
    return True
