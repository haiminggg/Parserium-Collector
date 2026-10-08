import argparse
import base64
import ipaddress
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

KEY_SIZE = 2048
VALIDITY = timedelta(hours=20)


def _private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _write_private_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(path, 0o600)


def _write_certificate(path: Path, certificate: x509.Certificate) -> None:
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def _leaf_certificate(
    *,
    hostnames: tuple[str, ...],
    leaf_key: rsa.RSAPrivateKey,
    ca_key: rsa.RSAPrivateKey,
    ca_certificate: x509.Certificate,
    not_before: datetime,
    not_after: datetime,
) -> x509.Certificate:
    if not hostnames:
        raise ValueError("At least one certificate hostname is required.")
    alternative_names: list[x509.GeneralName] = [
        x509.DNSName(hostname) for hostname in hostnames
    ]
    if "localhost" in hostnames:
        alternative_names.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    return (
        x509.CertificateBuilder()
        .subject_name(_name(hostnames[0]))
        .issuer_name(ca_certificate.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(alternative_names),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )


def generate(output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    if any(output_directory.iterdir()):
        raise ValueError("The verification PKI output directory must be empty.")

    not_before = datetime.now(UTC) - timedelta(minutes=2)
    not_after = not_before + VALIDITY
    ca_key = _private_key()
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(_name("Parserium ephemeral verification CA"))
        .issuer_name(_name("Parserium ephemeral verification CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write_private_key(output_directory / "ca-key.pem", ca_key)
    _write_certificate(output_directory / "ca.pem", ca_certificate)

    for hostname, hostnames in (
        ("hosted-api", ("hosted-api",)),
        ("verification-oidc", ("verification-oidc",)),
        ("minio", ("minio", "localhost")),
        ("test-firecrawl", ("test-firecrawl",)),
    ):
        leaf_key = _private_key()
        certificate = _leaf_certificate(
            hostnames=hostnames,
            leaf_key=leaf_key,
            ca_key=ca_key,
            ca_certificate=ca_certificate,
            not_before=not_before,
            not_after=not_after,
        )
        _write_private_key(output_directory / f"{hostname}-key.pem", leaf_key)
        _write_certificate(output_directory / f"{hostname}-cert.pem", certificate)

    signing_key = _private_key()
    _write_private_key(output_directory / "oidc-signing-key.pem", signing_key)
    (output_directory / "oidc-signing-public.pem").write_bytes(
        signing_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    client_secret_path = output_directory / "oidc-client-secret"
    client_secret_path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    os.chmod(client_secret_path, 0o600)

    access_key_path = output_directory / "minio-access-key"
    access_key_path.write_text(
        f"parserium-{secrets.token_urlsafe(24)}", encoding="utf-8"
    )
    os.chmod(access_key_path, 0o600)
    secret_key_path = output_directory / "minio-secret-key"
    secret_key_path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    os.chmod(secret_key_path, 0o600)

    firecrawl_bearer_path = output_directory / "firecrawl-test-bearer"
    firecrawl_bearer_path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    os.chmod(firecrawl_bearer_path, 0o600)

    credential_wrapping_key_path = (
        output_directory / "firecrawl-credential-wrapping-key"
    )
    credential_wrapping_key_path.write_text(
        base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        encoding="utf-8",
    )
    os.chmod(credential_wrapping_key_path, 0o600)

    discovery_fingerprint_path = output_directory / "discovery-fingerprint-secret"
    discovery_fingerprint_path.write_text(
        secrets.token_urlsafe(48), encoding="utf-8"
    )
    os.chmod(discovery_fingerprint_path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    generate(args.output_directory.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
