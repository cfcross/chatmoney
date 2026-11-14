"""Loopback TLS materials for Windows. Master key and TLS password use current-user DPAPI."""

import argparse
import base64
import ctypes
import hashlib
import ipaddress
import json
import os
import secrets
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(value, *, decrypt=False):
    buf = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    src, dst = Blob(len(value), buf), Blob()
    lib = ctypes.windll.crypt32
    fn = lib.CryptUnprotectData if decrypt else lib.CryptProtectData
    if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise OSError("Windows 无法读取此账户的受保护配置")
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(dst.data, ctypes.c_void_p))


def folder():
    project = str(Path(__file__).resolve().parents[1]).casefold()
    suffix = hashlib.sha256(project.encode()).hexdigest()[:12]
    return Path(os.environ["LOCALAPPDATA"]) / "ChatMoney" / suffix


def get_secrets(location, *, initialize=False):
    file = location / "protected.bin"
    if initialize and not file.exists() and (location / "localhost.crt").exists():
        raise RuntimeError("受保护主密钥已丢失，请恢复原备份，避免生成不匹配的密钥")
    if initialize and not file.exists():
        location.mkdir(parents=True, exist_ok=True)
        data = dict(
            master=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
            tls_password=secrets.token_urlsafe(40),
        )
        with file.open("xb") as f:
            f.write(protect(json.dumps(data).encode()))
    return json.loads(protect(file.read_bytes(), decrypt=True))


def create_cert(location, password):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ChatMoney localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    (location / "localhost.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(password.encode()),
        )
    )
    (location / "localhost.crt").write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["init", "https", "worker", "verify"])
    mode = parser.parse_args().mode
    location = folder()
    values = get_secrets(location, initialize=mode == "init")
    cert = location / "localhost.crt"
    if mode == "init":
        if not cert.exists():
            create_cert(location, values["tls_password"])
        print(cert)
        return
    if not os.environ.get("CHATMONEY_CREDENTIAL_ENCRYPTION_KEY"):
        os.environ["CHATMONEY_CREDENTIAL_ENCRYPTION_KEY"] = values["master"]
    if mode == "worker":
        from app.worker import main as worker
        import sys

        sys.argv = [sys.argv[0]]
        worker()
    elif mode == "https":
        import uvicorn

        uvicorn.run(
            "app.main:app",
            host="127.0.0.1",
            port=8443,
            ssl_keyfile=str(location / "localhost.key"),
            ssl_certfile=str(cert),
            ssl_keyfile_password=values["tls_password"],
            proxy_headers=False,
            access_log=False,
        )
    else:
        import httpx

        response = httpx.get(
            "https://localhost:8443/console", verify=str(cert), trust_env=False
        )
        response.raise_for_status()
        print("HTTPS 工作台可用")


if __name__ == "__main__":
    main()
