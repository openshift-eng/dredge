from pathlib import Path
from unittest.mock import patch

import pytest
import requests
import responses

from dredge.fetch_url import FetchError, NotFoundError, fetch_url

FIXTURES = Path(__file__).parent / "fixtures"


class TestFetchUrl:
    @responses.activate
    def test_returns_readable_bytes_stream(self):
        responses.get("http://example.com/page", body=b"hello world")

        with fetch_url("http://example.com/page") as body:
            assert body.read() == b"hello world"

    @responses.activate
    def test_raises_not_found_error_on_404(self):
        responses.get("http://example.com/missing", status=404)

        with pytest.raises(NotFoundError):
            with fetch_url("http://example.com/missing") as body:
                body.read()

    @responses.activate
    def test_raises_fetch_error_on_server_error(self):
        responses.get("http://example.com/broken", status=500)

        with pytest.raises(FetchError):
            with fetch_url("http://example.com/broken") as body:
                body.read()

    @responses.activate
    @patch("dredge.fetch_url._session.time.sleep")
    def test_retries_on_transient_failure(self, mock_sleep):
        responses.get(
            "http://example.com/flaky",
            body=responses.ConnectionError("connection reset"),
        )
        responses.get(
            "http://example.com/flaky",
            body=responses.ConnectionError("connection reset"),
        )
        responses.get("http://example.com/flaky", body=b"recovered")

        with fetch_url("http://example.com/flaky") as body:
            assert body.read() == b"recovered"

    @responses.activate
    @patch("dredge.fetch_url._session.time.sleep")
    def test_raises_after_retries_exhausted(self, mock_sleep):
        for _ in range(4):
            responses.get(
                "http://example.com/down",
                body=responses.ConnectionError("refused"),
            )

        with pytest.raises(FetchError):
            with fetch_url("http://example.com/down") as body:
                body.read()

    @responses.activate
    def test_stream_is_closed_after_context_exit(self):
        responses.get("http://example.com/page", body=b"data")

        with fetch_url("http://example.com/page") as body:
            pass

        assert body.closed

    @responses.activate
    def test_authenticates_on_403_with_oauth_proxy(self):
        base = "http://private.openshiftapps.com"
        oauth_base = "http://oauth-openshift.openshiftapps.com"
        idp_base = "http://idp.openshift.org"
        kerberos_host = "auth.redhat.com"
        kerberos_base = f"http://{kerberos_host}"

        oauth_403_html = (FIXTURES / "oauth_403.html").read_text()
        idp_selection_html = (FIXTURES / "oauth_authorize.html").read_text()

        # 1. Initial request → 403 with OAuth proxy login form
        responses.get(
            f"{base}/page",
            status=403,
            body=oauth_403_html,
            headers={"Set-Cookie": "_oauth_proxy=; Path=/"},
        )

        # 2. /oauth/start → 302 to OAuth authorize
        responses.get(
            f"{base}/oauth/start",
            status=302,
            headers={"Location": f"{oauth_base}/oauth/authorize?client_id=test"},
        )

        # 3. OAuth authorize → 200 with IDP selection page (HTML scraping)
        responses.get(
            f"{oauth_base}/oauth/authorize",
            status=200,
            body=idp_selection_html,
        )

        # 4. OAuth authorize with IDP → 302 to IDP provider
        responses.get(
            f"{oauth_base}/oauth/authorize",
            status=302,
            headers={"Location": f"{idp_base}/auth?client_id=test"},
        )

        # 5. IDP → 302 to Kerberos endpoint
        responses.get(
            f"{idp_base}/auth",
            status=302,
            headers={"Location": f"{kerberos_base}/auth/realms/EmployeeIDP/auth"},
        )

        # 6. Kerberos → 401 Negotiate challenge
        responses.get(
            f"{kerberos_base}/auth/realms/EmployeeIDP/auth",
            status=401,
            headers={"www-authenticate": "Negotiate"},
        )

        # 7. Kerberos with token → 302 back to IDP callback
        responses.get(
            f"{kerberos_base}/auth/realms/EmployeeIDP/auth",
            status=302,
            headers={"Location": f"{idp_base}/callback?code=authcode"},
        )

        # 8. IDP callback → 303 to approval page
        responses.get(
            f"{idp_base}/callback",
            status=303,
            headers={"Location": f"{idp_base}/approval?req=test"},
        )

        # 9. IDP approval → 303 to OAuth2 callback
        responses.get(
            f"{idp_base}/approval",
            status=303,
            headers={"Location": f"{oauth_base}/oauth2callback/RedHat_Internal_SSO?code=authcode"},
        )

        # 10. OAuth2 callback → 302 back to /oauth/authorize (with session cookie)
        responses.get(
            f"{oauth_base}/oauth2callback/RedHat_Internal_SSO",
            status=302,
            headers={"Location": f"{oauth_base}/oauth/authorize?client_id=test&idp=RedHat_Internal_SSO"},
        )

        # 11. OAuth authorize (revisit with session) → 302 to proxy callback
        responses.get(
            f"{oauth_base}/oauth/authorize",
            status=302,
            headers={"Location": f"{base}/oauth/callback?code=sha256~authcode"},
        )

        # 12. Proxy callback → sets _oauth_proxy cookie, redirects to original page
        responses.get(
            f"{base}/oauth/callback",
            status=302,
            headers={
                "Location": f"{base}/page",
                "Set-Cookie": "_oauth_proxy=valid-token; Path=/; Domain=private.openshiftapps.com",
            },
        )

        # 11. Auth chain follows redirect to page (chain finds cookie and terminates)
        responses.get(f"{base}/page", body=b"login complete")

        # 12. Retry original request with auth cookies → 200
        responses.get(f"{base}/page", body=b"authenticated")

        with patch("dredge.fetch_url._auth._generate_kerberos_token", return_value="fake-token"):
            with patch("dredge.fetch_url._auth._save_cookies"):
                with patch("dredge.fetch_url._auth._load_cached_cookies", return_value=None):
                    with fetch_url(f"{base}/page") as body:
                        assert body.read() == b"authenticated"

    def test_public_api_is_restricted(self):
        import dredge.fetch_url as module

        assert set(module.__all__) == {"fetch_url", "FetchError", "NotFoundError"}
