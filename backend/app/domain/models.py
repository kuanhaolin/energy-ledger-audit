from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TrustedCertificate:
    fingerprint: str
    signer_id: str
    site_id_hash: str
    pem: str
    enabled: bool


@dataclass(frozen=True)
class AnchorRecord:
    anchor_id: str
    manifest_id: str
    site_id_hash: str
    business_date: str
    sequence: int
    daily_root: str
    manifest_hash: str
    prev_manifest_hash: str | None
    amends_manifest_hash: str | None
    status: str
    tx_hash: str | None = None
    block_number: int | None = None
    block_hash: str | None = None
    transaction_index: int | None = None
    log_index: int | None = None
    submitter: str | None = None
    anchored_at: str | None = None
    receipt: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class AnchorSubmissionResult:
    anchor: AnchorRecord
    created: bool
