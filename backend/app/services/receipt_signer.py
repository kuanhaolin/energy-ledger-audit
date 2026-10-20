import base64
import hashlib
from datetime import UTC, datetime
from typing import Any

import rfc8785
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from app.config import Settings
from app.domain.models import AnchorRecord


class ReceiptSigner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _load_materials(self) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
        key_path = self.settings.notary_private_key_file
        certificate_path = self.settings.notary_certificate_file
        if key_path is None or certificate_path is None:
            raise ValueError("Receipt signing key or certificate is not configured")

        password = (
            self.settings.notary_private_key_password.encode("utf-8")
            if self.settings.notary_private_key_password
            else None
        )
        private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=password)
        if not isinstance(private_key, ec.EllipticCurvePrivateKey):
            raise TypeError("Receipt private key must be an EC key")
        certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
        certificate_key = certificate.public_key()
        if not isinstance(certificate_key, ec.EllipticCurvePublicKey):
            raise TypeError("Receipt certificate must contain an EC public key")
        if private_key.public_key().public_numbers() != certificate_key.public_numbers():
            raise ValueError("Receipt private key does not match its certificate")
        return private_key, certificate

    def validate_configuration(self) -> None:
        self._load_materials()

    def create(self, anchor: AnchorRecord, chain: dict[str, Any]) -> dict[str, Any]:
        private_key, certificate = self._load_materials()
        fingerprint = hashlib.sha256(
            certificate.public_bytes(serialization.Encoding.DER)
        ).hexdigest()

        receipt: dict[str, Any] = {
            "anchorId": anchor.anchor_id,
            "manifestId": anchor.manifest_id,
            "manifestHash": anchor.manifest_hash,
            "dailyRoot": anchor.daily_root,
            "status": "FINALIZED",
            "chain": {
                "network": self.settings.chain_name,
                "chainId": self.settings.chain_id,
                "consensus": self.settings.chain_consensus,
                "contractAddress": self.settings.anchor_contract_address,
                **{
                    key: value
                    for key, value in chain.items()
                    if key not in {"anchoredAt", "submitter", "transactionIndex"}
                },
            },
            "notary": {
                "org": self.settings.notary_org,
                "service": self.settings.notary_service,
                "certFingerprint": f"sha256:{fingerprint}",
                "alg": "ES256",
            },
            "receivedAt": anchor.created_at,
            "anchoredAt": chain["anchoredAt"],
        }
        signature_der = private_key.sign(rfc8785.dumps(receipt), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(signature_der)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        receipt["notary"]["signature"] = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        return receipt


def block_time_iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")
