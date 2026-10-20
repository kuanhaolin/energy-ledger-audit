import base64
import hashlib
from datetime import datetime
from typing import Any, Protocol

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from app.domain.errors import cert_unknown, mtls_invalid, signature_invalid
from app.domain.models import TrustedCertificate
from app.services.manifest_validation import canonical_manifest_bytes


class CertificateLookup(Protocol):
    async def get_certificate(self, fingerprint: str) -> TrustedCertificate | None: ...


def certificate_fingerprint(pem: str) -> str:
    certificate = x509.load_pem_x509_certificate(pem.encode("utf-8"))
    digest = hashlib.sha256(
        certificate.public_bytes(serialization.Encoding.DER)
    ).hexdigest()
    return f"sha256:{digest}"


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("signedAt must contain a timezone")
    return parsed


def _raw_es256_to_der(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode(value + padding)
    if len(raw) != 64:
        raise ValueError("ES256 signature must contain 64 raw bytes")
    return encode_dss_signature(
        int.from_bytes(raw[:32], "big"),
        int.from_bytes(raw[32:], "big"),
    )


class ManifestSignatureVerifier:
    def __init__(self, certificates: CertificateLookup) -> None:
        self.certificates = certificates

    async def verify_platform_signature(
        self,
        manifest: dict[str, Any],
        mtls_fingerprint: str | None = None,
        mtls_site_id_hash: str | None = None,
    ) -> str:
        if mtls_fingerprint:
            mtls_certificate = await self.certificates.get_certificate(mtls_fingerprint.lower())
            if mtls_certificate is None or not mtls_certificate.enabled:
                raise mtls_invalid("mTLS client certificate is not trusted")
            if mtls_certificate.site_id_hash != manifest["siteIdHash"]:
                raise mtls_invalid("mTLS client certificate belongs to a different site")
        if mtls_site_id_hash and mtls_site_id_hash != manifest["siteIdHash"]:
            raise mtls_invalid("mTLS client certificate belongs to a different site")

        platform_signatures = [
            item for item in manifest["signatures"] if item.get("signer") == "PLATFORM"
        ]
        if not platform_signatures:
            raise signature_invalid("At least one PLATFORM signature is required")

        saw_trusted_certificate = False
        unknown_fingerprint = platform_signatures[0]["certFingerprint"]
        failures: list[str] = []

        for signature in platform_signatures:
            fingerprint = signature["certFingerprint"].lower()
            trusted = await self.certificates.get_certificate(fingerprint)
            if trusted is None or not trusted.enabled:
                failures.append(f"unknown certificate: {fingerprint}")
                continue
            saw_trusted_certificate = True

            try:
                self._verify_one(
                    manifest,
                    signature,
                    trusted,
                )
                return fingerprint
            except (InvalidSignature, ValueError, TypeError) as error:
                failures.append(str(error) or error.__class__.__name__)

        if not saw_trusted_certificate:
            raise cert_unknown(unknown_fingerprint)
        raise signature_invalid(
            "No valid PLATFORM signature was found",
            {"reasons": failures},
        )

    @staticmethod
    def _verify_one(
        manifest: dict[str, Any],
        signature: dict[str, Any],
        trusted: TrustedCertificate,
    ) -> None:
        fingerprint = signature["certFingerprint"].lower()
        if certificate_fingerprint(trusted.pem) != fingerprint:
            raise ValueError("registered certificate fingerprint does not match PEM")
        if trusted.signer_id != signature["signerId"]:
            raise ValueError("signerId does not match the registered certificate")
        if trusted.site_id_hash != manifest["siteIdHash"]:
            raise ValueError("signing certificate is not registered for this siteIdHash")
        certificate = x509.load_pem_x509_certificate(trusted.pem.encode("utf-8"))
        signed_at = _parse_timestamp(signature["signedAt"])
        if not (certificate.not_valid_before_utc <= signed_at <= certificate.not_valid_after_utc):
            raise ValueError("certificate was not valid at signedAt")

        public_key = certificate.public_key()
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise TypeError("certificate does not contain an EC public key")

        public_key.verify(
            _raw_es256_to_der(signature["signature"]),
            canonical_manifest_bytes(manifest),
            ec.ECDSA(hashes.SHA256()),
        )
