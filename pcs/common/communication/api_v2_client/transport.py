import ssl
from abc import ABC, abstractmethod
from http.client import HTTPResponse
from ipaddress import AddressValueError, IPv6Address
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
    def host_label(self) -> str:
        """
        Human readable endpoint identifier.
        """

    def build_request(self, path: str, json_body: bytes | None) -> Request:
        """
        Create a request
        """
        headers = (
            {"Content-Type": "application/json"}
            if json_body is not None
            else {}
        )
        # GET or POST is automatically used depending on the json_body
        return Request(
            self._build_url(path),
            data=json_body,
            headers={**headers, **self._auth_headers},
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

    @property
    @abstractmethod
    def _auth_headers(self) -> dict[str, str]:
        """
        Authentication headers to attach to every request
        """


class TcpTransport(Transport):
    """
    Talk to a remote node over HTTPS using a token cookie for auth.
    """

    # TODO: this should use RequestTarget instead of these 4 values
    def __init__(self, addr: str, port: int, token: str, node_name: str):
        self._host = f"{_format_addr_for_url(addr)}:{port}"
        self._token = token
        self._node_name = node_name

    @property
    def host_label(self) -> str:
        return self._host

    def _build_url(self, path: str) -> str:
        return f"https://{self._host}{path}"

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Cookie": f"token={self._token}"}

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
    def host_label(self) -> str:
        return self._socket_path

    def _build_url(self, path: str) -> str:
        # The host is irrelevant for a Unix socket connection but urllib
        # requires a non-empty host; the connection ignores it and it only
        # ends up in the Host header. The actual endpoint is the socket path
        # carried by the handler.
        return f"unix://localhost{path}"

    @property
    def _auth_headers(self) -> dict[str, str]:
        # No auth headers needed, the daemon authenticates the caller from the
        # socket peer credentials (SO_PEERCRED)
        return {}

    def open(self, request: Request, timeout: float) -> HTTPResponse:
        return self._opener.open(request, timeout=timeout)
