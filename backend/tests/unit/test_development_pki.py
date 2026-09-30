import ipaddress
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from parserium_collector.cli.development_pki import generate_hosted_local_pki


def _verify_issued_by(certificate: x509.Certificate, ca: x509.Certificate) -> None:
    public_key = ca.public_key()
    assert isinstance(public_key, rsa.RSAPublicKey)
    public_key.verify(
        certificate.signature,
        certificate.tbs_certificate_bytes,
        padding.PKCS1v15(),
        certificate.signature_hash_algorithm,
    )


def test_generates_bounded_hosted_local_pki(tmp_path: Path) -> None:
    output = tmp_path / "pki"

    generate_hosted_local_pki(output)

    expected = {
        "ca.pem",
        "ca-key.pem",
        "parserium-cert.pem",
        "parserium-key.pem",
        "minio-cert.pem",
        "minio-key.pem",
    }
    assert {path.name for path in output.iterdir()} == expected

    ca = x509.load_pem_x509_certificate((output / "ca.pem").read_bytes())
    assert ca.subject.rfc4514_string() == "CN=Parserium hosted-local development CA"
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value == (
        x509.BasicConstraints(ca=True, path_length=0)
    )

    parserium = x509.load_pem_x509_certificate((output / "parserium-cert.pem").read_bytes())
    parserium_sans = parserium.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert parserium_sans.get_values_for_type(x509.DNSName) == ["localhost"]
    assert parserium_sans.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("127.0.0.1")]
    _verify_issued_by(parserium, ca)

    minio = x509.load_pem_x509_certificate((output / "minio-cert.pem").read_bytes())
    minio_sans = minio.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert minio_sans.get_values_for_type(x509.DNSName) == ["minio", "localhost"]
    assert minio_sans.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("127.0.0.1")]
    _verify_issued_by(minio, ca)

    assert not list(output.glob(".*.tmp"))


def test_rejects_nonempty_output_directory(tmp_path: Path) -> None:
    output = tmp_path / "pki"
    output.mkdir()
    existing = output / "keep.txt"
    existing.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="must be empty"):
        generate_hosted_local_pki(output)

    assert existing.read_text(encoding="utf-8") == "keep"
    assert {path.name for path in output.iterdir()} == {"keep.txt"}
