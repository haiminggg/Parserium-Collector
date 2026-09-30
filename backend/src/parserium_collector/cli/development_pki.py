import argparse
import ipaddress
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_COMMON_NAME = "Parserium hosted-local development CA"
CA_VALIDITY = timedelta(days=5 * 365)
LEAF_VALIDITY = timedelta(days=365)
KEY_SIZE = 2048


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _atomic_bytes(path: Path, value: bytes, *, private: bool = False) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(value)
    if private:
        os.chmod(temporary, 0o600)
    temporary.replace(path)


def _leaf(
    *,
    common_name: str,
    dns_names: tuple[str, ...],
    ip_addresses: tuple[str, ...],
    key: rsa.RSAPrivateKey,
    ca_key: rsa.RSAPrivateKey,
    ca: x509.Certificate,
    not_before: datetime,
    not_after: datetime,
) -> x509.Certificate:
    alternative_names: list[x509.GeneralName] = [x509.DNSName(value) for value in dns_names]
    alternative_names.extend(x509.IPAddress(ipaddress.ip_address(value)) for value in ip_addresses)
    return (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
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
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(x509.SubjectAlternativeName(alternative_names), critical=False)
        .sign(ca_key, hashes.SHA256())
    )


def generate_hosted_local_pki(
    output_directory: Path,
    *,
    now: datetime | None = None,
) -> None:
    resolved_now = now or datetime.now(UTC)
    output_directory.mkdir(parents=True, exist_ok=True)
    if any(output_directory.iterdir()):
        raise ValueError("The hosted-local PKI output directory must be empty.")
    not_before = resolved_now - timedelta(minutes=5)
    ca_key = _key()
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name(CA_COMMON_NAME))
        .issuer_name(_name(CA_COMMON_NAME))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(resolved_now + CA_VALIDITY)
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
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    parserium_key = _key()
    parserium = _leaf(
        common_name="localhost",
        dns_names=("localhost",),
        ip_addresses=("127.0.0.1",),
        key=parserium_key,
        ca_key=ca_key,
        ca=ca,
        not_before=not_before,
        not_after=resolved_now + LEAF_VALIDITY,
    )
    minio_key = _key()
    minio = _leaf(
        common_name="minio",
        dns_names=("minio", "localhost"),
        ip_addresses=("127.0.0.1",),
        key=minio_key,
        ca_key=ca_key,
        ca=ca,
        not_before=not_before,
        not_after=resolved_now + LEAF_VALIDITY,
    )
    private_format = serialization.PrivateFormat.PKCS8
    no_encryption = serialization.NoEncryption()
    _atomic_bytes(
        output_directory / "ca-key.pem",
        ca_key.private_bytes(serialization.Encoding.PEM, private_format, no_encryption),
        private=True,
    )
    _atomic_bytes(
        output_directory / "ca.pem",
        ca.public_bytes(serialization.Encoding.PEM),
    )
    _atomic_bytes(
        output_directory / "parserium-key.pem",
        parserium_key.private_bytes(
            serialization.Encoding.PEM,
            private_format,
            no_encryption,
        ),
        private=True,
    )
    _atomic_bytes(
        output_directory / "parserium-cert.pem",
        parserium.public_bytes(serialization.Encoding.PEM),
    )
    _atomic_bytes(
        output_directory / "minio-key.pem",
        minio_key.private_bytes(serialization.Encoding.PEM, private_format, no_encryption),
        private=True,
    )
    _atomic_bytes(
        output_directory / "minio-cert.pem",
        minio.public_bytes(serialization.Encoding.PEM),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    arguments = parser.parse_args()
    generate_hosted_local_pki(arguments.output_directory.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
