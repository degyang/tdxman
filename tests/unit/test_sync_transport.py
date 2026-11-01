"""同步 transport 回归测试。"""

from unittest.mock import patch

from tdxman.exceptions import TdxConnectionError
from tdxman.transport.sync import TdxConnection, ping_all, ping_host


class _FakeSocket:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.connected_to: tuple[str, int] | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, address: tuple[str, int]) -> None:
        self.connected_to = address

    def close(self) -> None:
        self.closed = True


def test_sync_connection_closes_socket_when_setup_fails() -> None:
    sock = _FakeSocket()
    conn = TdxConnection("127.0.0.1", port=7709, timeout=0.2)

    with (
        patch("tdxman.transport.sync.socket.socket", return_value=sock),
        patch.object(
            TdxConnection,
            "_send_setup",
            side_effect=TdxConnectionError("setup failed"),
        ),
    ):
        try:
            conn.connect()
        except TdxConnectionError as exc:
            assert "setup failed" in str(exc)
        else:  # pragma: no cover - 防御性断言
            raise AssertionError("expected setup failure")

    assert sock.timeout == 0.2
    assert sock.connected_to == ("127.0.0.1", 7709)
    assert sock.closed is True
    assert conn._sock is None


def test_ping_host_returns_none_when_server_closes_during_handshake() -> None:
    class ClosingSocket(_FakeSocket):
        def sendall(self, data: bytes) -> None:
            pass

        def recv(self, n: int) -> bytes:
            return b""

    sock = ClosingSocket()
    with patch("tdxman.transport.sync.socket.socket", return_value=sock):
        assert ping_host("127.0.0.1", port=7709, timeout=0.2) is None

    assert sock.closed is True


def test_ping_all_ignores_a_handshake_failure_from_one_host() -> None:
    def ping_with_one_closed_host(host: str, port: int, timeout: float) -> float:
        if host == "closed":
            raise TdxConnectionError("连接被服务器关闭")
        return 0.01

    with patch(
        "tdxman.transport.sync.ping_host",
        side_effect=ping_with_one_closed_host,
    ):
        assert ping_all(["available", "closed"], port=7709, timeout=0.2) == [("available", 0.01)]


def test_quote_session_sends_only_first_setup_without_changing_default() -> None:
    from unittest.mock import MagicMock, call

    from tdxman.commands.setup import SETUP_COMMANDS

    for quote_session, expected in ((True, SETUP_COMMANDS[:1]), (False, SETUP_COMMANDS)):
        sock = MagicMock()
        conn = TdxConnection("127.0.0.1", quote_session=quote_session)
        conn._sock = sock
        with (
            patch.object(conn, "_recv_exact", return_value=b""),
            patch("tdxman.transport.sync.parse_header", return_value=MagicMock(zipsize=0)),
        ):
            conn._send_setup()
        assert sock.sendall.call_args_list == [call(packet) for packet in expected]


def test_quote_session_survives_client_reconnect() -> None:
    from unittest.mock import MagicMock

    from tdxman.client import TdxClient

    client = TdxClient("127.0.0.1", port=7709, timeout=1, quote_session=True, heartbeat_interval=0)
    client._conn = MagicMock()
    client._conn.execute.side_effect = TdxConnectionError("disconnected")
    with patch("tdxman.client.TdxConnection") as connection, patch("tdxman.client.time.sleep"):
        connection.return_value.execute.return_value = ["reconnected"]
        assert client._execute(MagicMock()) == ["reconnected"]
        connection.assert_called_once_with("127.0.0.1", 7709, 1, quote_session=True)
