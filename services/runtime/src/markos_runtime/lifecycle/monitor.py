"""Independent synthetic monitor/fallback algorithm; only local fixture destinations."""

from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


def monitor_once(health_url: str, primary_url: str, secondary_url: str) -> str:
    for value in (health_url, primary_url, secondary_url):
        url = urlsplit(value)
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("synthetic loopback monitor endpoints required")
    opener = build_opener(NoRedirect())
    try:
        with opener.open(health_url, timeout=1) as response:
            if response.status == 200:
                return "healthy"
    except (URLError, OSError):
        pass
    for name, destination in [("primary", primary_url), ("secondary", secondary_url)]:
        try:
            request = Request(
                destination,
                data=b'{"event":"synthetic_runtime_unavailable"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with opener.open(request, timeout=1) as response:
                if response.status == 200:
                    return name
        except (URLError, OSError):
            pass
    return "failed"
