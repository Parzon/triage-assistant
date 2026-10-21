"""The database socket option that ends connections whose address vanished
(ADR-0027). The failure itself is reproduced by `make drills d=address-change`."""

import socket

import asyncpg  # type: ignore[import-untyped]
import pytest

from app.db import connection_class, set_tcp_user_timeout


@pytest.mark.skipif(not hasattr(socket, "TCP_USER_TIMEOUT"), reason="a Linux socket option")
def test_sets_tcp_user_timeout_in_milliseconds() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        set_tcp_user_timeout(sock, 10.0)
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT) == 10_000


def test_leaves_a_unix_socket_alone() -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        set_tcp_user_timeout(sock, 10.0)  # a TCP option on a Unix socket would raise


def test_a_transport_without_a_socket_is_fine() -> None:
    set_tcp_user_timeout(None, 10.0)


def test_the_engine_gets_an_asyncpg_connection_class() -> None:
    assert issubclass(connection_class(10.0), asyncpg.Connection)
