import ssl
from abc import ABC, abstractmethod
from collections.abc import Mapping
from http.client import HTTPResponse
from ipaddress import AddressValueError, IPv6Address
from urllib.parse import urlencode
from urllib.request import Request, build_opener, urlopen

from .handler import UnixSocketHttpHandler

# There can be self signed certificates.
_UNVERIFIED_SSL_CONTEXT = ssl._create_unverified_context()  # noqa: SLF001


def _format_addr_for_url(addr: str) -> str:
    try:
        IPv6Address(addr)
        return f"[{addr}]"
    except AddressValueError:
        return addr


class Transport(ABC):
    """
    Encapsulates how an APIv2 request is addressed and dispatched.
    """

    @property
    @abstractmethod
    def label(self) -> str:
        """
        Human readable endpoint identifier.
        """

    @property
    @abstractmethod
    def respects_proxy(self) -> bool:
        """
        Whether proxy settings from environment variables affect the requests.
        """

    def build_request(
        self,
        path: str,
        json_body: bytes | None = None,
        query_params: Mapping[str, str] | None = None,
    ) -> Request:
        """
        Create a request
        """
        # Make sure the path always starts with slash
        if not path.startswith("/"):
            raise AssertionError(f"URL path '{path}' does not start with '/'")

        url = self._build_url(path)
        if query_params:
            url += f"?{urlencode(query_params)}"

        headers = (
            {"Content-Type": "application/json"}
            if json_body is not None
            else {}
        )

        # GET or POST is automatically used depending on the json_body
        return Request(
            url=url, data=json_body, headers=self._build_headers(headers)
        )

    @abstractmethod
    def open(self, request: Request, timeout: float) -> HTTPResponse:
        """
        Send request and return the raw response context manager.
        """

    @abstractmethod
    def _build_url(self, path: str) -> str:
        """
        Return valid URL for the given transport

        path -- path for the endpoint, beginning with '/'
        """

    def _build_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """
        Create requests headers, add any necessary headers needed for specific
        transports
        """
        return headers


class TcpTransport(Transport):
    """
    Talk to a remote node over HTTPS using a token cookie for auth.
    """

    # TODO: this should use RequestTarget instead of these values
    def __init__(self, addr: str, port: int, token: str):
        self._host = f"{_format_addr_for_url(addr)}:{port}"
        self._token = token

    @property
    def label(self) -> str:
        return self._host

    @property
    def respects_proxy(self) -> bool:
        # urlopen uses the default opener which includes ProxyHandler
        return True

    def _build_url(self, path: str) -> str:
        return f"https://{self._host}{path}"

    def _build_headers(self, headers: dict[str, str]) -> dict[str, str]:
        return {**headers, "Cookie": f"token={self._token}"}

    def open(self, request: Request, timeout: float) -> HTTPResponse:
        return urlopen(
            request, context=_UNVERIFIED_SSL_CONTEXT, timeout=timeout
        )


class UnixSocketTransport(Transport):
    """
    Talk to the local pcsd over its Unix domain socket.
    """

    def __init__(self, socket_path: str):
        self._socket_path = socket_path
        self._opener = build_opener(UnixSocketHttpHandler(socket_path))

    @property
    def label(self) -> str:
        return self._socket_path

    @property
    def respects_proxy(self) -> bool:
        # Requests are sent directly over the unix socket by
        # UnixSocketHttpHandler, the unix:// scheme is not proxied
        return False

    def _build_url(self, path: str) -> str:
        # The host is irrelevant for a Unix socket connection but urllib
        # requires a non-empty host; the connection ignores it and it only
        # ends up in the Host header. The actual endpoint is the socket path
        # carried by the handler.
        return f"unix://localhost{path}"

    def open(self, request: Request, timeout: float) -> HTTPResponse:
        return self._opener.open(request, timeout=timeout)
