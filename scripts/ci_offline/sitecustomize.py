"""Loaded only during CI pytest, including child Python API and worker processes."""
import ipaddress
import sys


def local_network(event, args):
    if event not in {"socket.connect", "socket.getaddrinfo"}:
        return
    if event == "socket.connect":
        if not isinstance(args[1], tuple):  # Local Unix socket
            return
        host = args[1][0]
    else:
        host = args[0]
    if host in {None, "", "localhost", b"localhost"}:
        return
    if isinstance(host, bytes):
        host = host.decode("ascii")
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise PermissionError("This test suite only permits loopback networking")


sys.addaudithook(local_network)
