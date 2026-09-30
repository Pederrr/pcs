import socket
from http.client import HTTPConnection, HTTPResponse
from typing import Any
from urllib.request import AbstractHTTPHandler, Request


class _UnixSocketHttpConnection(HTTPConnection):
    """
    HTTP connection over a Unix domain socket instead of a TCP socket.
    """

    def __init__(
        self,
        host: str,
        socket_path: str | None = None,
        **kwargs: Any,
    ):
        # `host` is required by `HTTPConnection` (it ends up in the `Host`
        # header) but the actual endpoint is the Unix socket at `socket_path`.
        super().__init__(host, **kwargs)

        # We have to provide default value for `socket_path` variable to make
        # this class work properly with urllib. But we always want the path
        # to be present
        if socket_path is None:
            raise ValueError(
                "`socket_path` is required for unix socket connections"
            )
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


class UnixSocketHttpHandler(AbstractHTTPHandler):
    """
    urllib handler routing `unix://` requests through a Unix socket.
    """

    def __init__(self, socket_path: str, debuglevel: int | None = None):
        super().__init__(debuglevel)
        self._socket_path = socket_path

    # urllib wires schemes to handlers by method names. So unix:// urls will be
    # opened with this method
    def unix_open(self, req: Request) -> HTTPResponse:
        return self.do_open(
            _UnixSocketHttpConnection,
            req,
            socket_path=self._socket_path,
        )

    # Reuse the HTTP request preprocessing (Host, Content-Length, ...) for the
    # custom 'unix://' scheme.
    unix_request = AbstractHTTPHandler.do_request_
