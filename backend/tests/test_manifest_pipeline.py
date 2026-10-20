import base64
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import rfc8785
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.x509.oid import NameOID

from app.api.routes import (
    anchor_accepted_response,
    anchor_response,
    anchor_status_response,
    normalize_site_id_hash,
    router,
    site_date_response,
    validate_idempotency_key,
)
from app.config import Settings
from app.domain.errors import NotaryError
from app.domain.models import AnchorRecord, TrustedCertificate
from app.repository import Repository
from app.services.anchor_service import AnchorService
from app.services.manifest_validation import calculate_manifest_hash
from app.services.receipt_signer import ReceiptSigner
from app.services.signature_verifier import certificate_fingerprint

FIXTURE_PATH = Path(__file__).parents[2] / "frontend" / "fixtures" / "appendix_c.json"
TEST_FIXTURE_PATH = Path(__file__).parents[2] / "frontend" / "fixtures" / "test.json"


@pytest.fixture
def fixture_data() -> dict:
    if not FIXTURE_PATH.exists():
        pytest.skip("本機附錄 C 測試資料未提供")
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
async def pipeline(tmp_path: Path, fixture_data: dict):
    settings = Settings(database_path=str(tmp_path / "notary.db"), anchor_mode="validate-only")
    repository = Repository(settings.database_file)
    await repository.initialize()

    manifest = fixture_data["manifest"]
    pem = fixture_data["certificate"]["pem"]
    await repository.upsert_certificate(
        TrustedCertificate(
            fingerprint=certificate_fingerprint(pem),
            signer_id=manifest["signatures"][0]["signerId"],
            site_id_hash=manifest["siteIdHash"],
            pem=pem,
            enabled=True,
        )
    )
    await repository.create_anchor(
        AnchorRecord(
            anchor_id="anc_previous",
            manifest_id=manifest["prevManifestId"],
            site_id_hash=manifest["siteIdHash"],
            business_date="2026-09-02",
            sequence=manifest["sequence"] - 1,
            daily_root=manifest["prevDailyRoot"],
            manifest_hash=manifest["prevManifestHash"],
            prev_manifest_hash="0" * 64,
            amends_manifest_hash=None,
            status="FINALIZED",
        )
    )
    return AnchorService(settings, repository), repository


def test_appendix_c_manifest_hash(fixture_data: dict) -> None:
    manifest = fixture_data["manifest"]
    assert calculate_manifest_hash(manifest) == manifest["manifestHash"]


async def test_appendix_c_passes_steps_one_to_five(pipeline, fixture_data: dict) -> None:
    service, _repository = pipeline
    result = await service.submit(fixture_data["manifest"], None, None)
    assert result.created is True
    assert result.anchor.status == "VALIDATED"
    assert result.anchor.sequence == 247
    assert result.anchor.receipt is None


async def test_sequence_one_fixture_passes_steps_one_to_five(tmp_path: Path) -> None:
    if not TEST_FIXTURE_PATH.exists():
        pytest.skip("本機序號測試資料未提供")
    fixture = json.loads(TEST_FIXTURE_PATH.read_text(encoding="utf-8"))
    manifest = fixture["manifest"]
    pem = fixture["certificate"]["pem"]
    settings = Settings(
        database_path=str(tmp_path / "sequence-one.db"),
        anchor_mode="validate-only",
    )
    repository = Repository(settings.database_file)
    await repository.initialize()
    await repository.upsert_certificate(
        TrustedCertificate(
            fingerprint=certificate_fingerprint(pem),
            signer_id=manifest["signatures"][0]["signerId"],
            site_id_hash=manifest["siteIdHash"],
            pem=pem,
            enabled=True,
        )
    )

    result = await AnchorService(settings, repository).submit(manifest, None, None)

    assert result.created is True
    assert result.anchor.status == "VALIDATED"
    assert result.anchor.sequence == 1
    assert result.anchor.prev_manifest_hash is None


async def test_hash_tampering_is_rejected(pipeline, fixture_data: dict) -> None:
    service, _repository = pipeline
    manifest = copy.deepcopy(fixture_data["manifest"])
    manifest["dailyRoot"] = "0" * 64
    with pytest.raises(NotaryError) as caught:
        await service.submit(manifest, None, None)
    assert caught.value.status_code == 422
    assert caught.value.code == "MANIFEST_HASH_MISMATCH"


async def test_chain_break_returns_expected_values(pipeline, fixture_data: dict) -> None:
    service, _repository = pipeline
    manifest = copy.deepcopy(fixture_data["manifest"])
    manifest["sequence"] = 999
    manifest["manifestHash"] = calculate_manifest_hash(manifest)
    with pytest.raises(NotaryError) as caught:
        await service.submit(manifest, None, None)
    assert caught.value.code == "SIGNATURE_INVALID"


async def test_chain_break_after_valid_resigning_is_detected(
    pipeline, fixture_data: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _repository = pipeline
    manifest = copy.deepcopy(fixture_data["manifest"])
    manifest["sequence"] = 999
    manifest["manifestHash"] = calculate_manifest_hash(manifest)

    async def accept_signature(*_args, **_kwargs):
        return manifest["signatures"][0]["certFingerprint"]

    monkeypatch.setattr(service.signatures, "verify_platform_signature", accept_signature)
    with pytest.raises(NotaryError) as caught:
        await service.submit(manifest, None, None)
    assert caught.value.status_code == 409
    assert caught.value.code == "CHAIN_BROKEN"
    assert caught.value.details["expectedSequence"] == 247
    assert caught.value.details["expectedPrevManifestHash"] == manifest["prevManifestHash"]


async def test_identical_retry_is_idempotent(pipeline, fixture_data: dict) -> None:
    service, _repository = pipeline
    first = await service.submit(fixture_data["manifest"], None, None)
    second = await service.submit(fixture_data["manifest"], None, None)
    assert first.created is True
    assert second.created is False
    assert second.anchor.anchor_id == first.anchor.anchor_id


def test_final_receipt_is_signed_over_jcs_without_signature(tmp_path: Path) -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Notary")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    key_path = tmp_path / "key.pem"
    cert_path = tmp_path / "cert.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    settings = Settings(
        notary_private_key_path=str(key_path),
        notary_private_key_password="",
        notary_certificate_path=str(cert_path),
        anchor_contract_address="0x0000000000000000000000000000000000000001",
    )
    anchor = AnchorRecord(
        anchor_id="anc_receipt",
        manifest_id="site:2026-09-03",
        site_id_hash=f"sha256:{'1' * 64}",
        business_date="2026-09-03",
        sequence=1,
        daily_root="2" * 64,
        manifest_hash="3" * 64,
        prev_manifest_hash=None,
        amends_manifest_hash=None,
        status="FINALIZED",
        created_at="2026-09-03T18:02:14Z",
    )
    chain = {
        "txHash": f"0x{'4' * 64}",
        "blockNumber": 123,
        "blockHash": f"0x{'5' * 64}",
        "blockTimestamp": "2026-09-03T18:03:05Z",
        "transactionIndex": 0,
        "logIndex": 0,
        "submitter": "0x0000000000000000000000000000000000000002",
        "anchoredAt": "2026-09-03T18:03:05Z",
    }
    receipt = ReceiptSigner(settings).create(anchor, chain)
    assert receipt["chain"]["consensus"] == "Proof of Stake"
    assert "transactionIndex" not in receipt["chain"]
    assert receipt["chain"]["blockTimestamp"] == "2026-09-03T18:03:05Z"
    assert receipt["anchoredAt"] == "2026-09-03T18:03:05Z"
    signature_text = receipt["notary"].pop("signature")
    raw = base64.urlsafe_b64decode(signature_text + "==")
    signature_der = encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    private_key.public_key().verify(
        signature_der,
        rfc8785.dumps(receipt),
        ec.ECDSA(hashes.SHA256()),
    )


@pytest.mark.parametrize(
    ("internal_status", "external_status"),
    [
        ("VALIDATED", "VERIFIED"),
        ("QUEUED", "VERIFIED"),
        ("SUBMITTING", "SUBMITTED"),
        ("FINALIZED", "FINALIZED"),
        ("FAILED", "REJECTED"),
    ],
)
def test_anchor_response_uses_srs_status_names(
    internal_status: str, external_status: str
) -> None:
    anchor = AnchorRecord(
        anchor_id="anc_status",
        manifest_id="site:2026-09-03",
        site_id_hash=f"sha256:{'1' * 64}",
        business_date="2026-09-03",
        sequence=1,
        daily_root="2" * 64,
        manifest_hash="3" * 64,
        prev_manifest_hash=None,
        amends_manifest_hash=None,
        status=internal_status,
        error_code="CHAIN_SUBMISSION_FAILED" if internal_status == "FAILED" else None,
        error_message="RPC connection failed" if internal_status == "FAILED" else None,
    )

    response = anchor_response(anchor)

    assert response["status"] == external_status
    if external_status == "REJECTED":
        assert response["error"] == {
            "code": "CHAIN_SUBMISSION_FAILED",
            "message": "Blockchain submission failed. Contact the service operator.",
        }
    else:
        assert "error" not in response


def test_new_anchor_response_is_received() -> None:
    anchor = AnchorRecord(
        anchor_id="anc_received",
        manifest_id="site:2026-09-03",
        site_id_hash=f"sha256:{'1' * 64}",
        business_date="2026-09-03",
        sequence=1,
        daily_root="2" * 64,
        manifest_hash="3" * 64,
        prev_manifest_hash=None,
        amends_manifest_hash=None,
        status="QUEUED",
        created_at="2026-09-03T18:02:14Z",
    )

    assert anchor_accepted_response(anchor) == {
        "anchorId": "anc_received",
        "manifestHash": "3" * 64,
        "status": "RECEIVED",
        "receivedAt": "2026-09-03T18:02:14Z",
    }


def test_duplicate_anchor_response_only_returns_current_status() -> None:
    anchor = AnchorRecord(
        anchor_id="anc_existing",
        manifest_id="site:2026-09-03",
        site_id_hash=f"sha256:{'1' * 64}",
        business_date="2026-09-03",
        sequence=1,
        daily_root="2" * 64,
        manifest_hash="3" * 64,
        prev_manifest_hash=None,
        amends_manifest_hash=None,
        status="FINALIZED",
        created_at="2026-09-03T18:02:14Z",
        receipt={"status": "FINALIZED", "chain": {"txHash": "0x123"}},
    )

    assert anchor_status_response(anchor) == {
        "anchorId": "anc_existing",
        "manifestHash": "3" * 64,
        "status": "FINALIZED",
        "receivedAt": "2026-09-03T18:02:14Z",
    }


def test_idempotency_key_must_match_manifest_hash() -> None:
    manifest = {"manifestHash": "a" * 64}
    validate_idempotency_key("a" * 64, manifest)

    with pytest.raises(NotaryError) as missing:
        validate_idempotency_key(None, manifest)
    assert missing.value.status_code == 400
    assert missing.value.code == "SCHEMA_INVALID"

    with pytest.raises(NotaryError) as mismatch:
        validate_idempotency_key("b" * 64, manifest)
    assert mismatch.value.status_code == 400
    assert mismatch.value.code == "SCHEMA_INVALID"


def test_manifest_lookup_uses_srs_query_parameter_route() -> None:
    route = next(
        item
        for item in router.routes
        if item.path == "/anchors" and "GET" in (item.methods or set())
    )

    assert [parameter.alias for parameter in route.dependant.query_params] == [
        "manifestHash",
        "siteIdHash",
        "businessDate",
    ]
    assert not any(item.path.startswith("/anchors/by-manifest") for item in router.routes)


async def test_repository_lists_anchors_by_site_and_date(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "site-date.db")
    await repository.initialize()
    site_id_hash = f"sha256:{'1' * 64}"

    for sequence in (2, 1):
        await repository.create_anchor(
            AnchorRecord(
                anchor_id=f"anc_{sequence}",
                manifest_id=f"site:2026-09-03:{sequence}",
                site_id_hash=site_id_hash,
                business_date="2026-09-03",
                sequence=sequence,
                daily_root=str(sequence) * 64,
                manifest_hash=str(sequence + 2) * 64,
                prev_manifest_hash=None,
                amends_manifest_hash=None,
                status="FINALIZED",
            )
        )

    anchors = await repository.list_by_site_date(site_id_hash, "2026-09-03")

    assert [anchor.sequence for anchor in anchors] == [1, 2]


def test_site_date_response_marks_latest_finalized_as_current() -> None:
    site_id_hash = f"sha256:{'1' * 64}"
    anchors = [
        AnchorRecord(
            anchor_id=f"anc_{sequence}",
            manifest_id=f"site:2026-09-03:{sequence}",
            site_id_hash=site_id_hash,
            business_date="2026-09-03",
            sequence=sequence,
            daily_root=str(sequence) * 64,
            manifest_hash=str(sequence + 2) * 64,
            prev_manifest_hash=None,
            amends_manifest_hash=None,
            status=status,
        )
        for sequence, status in ((1, "FINALIZED"), (2, "FINALIZED"), (3, "QUEUED"))
    ]

    response = site_date_response(site_id_hash, "2026-09-03", anchors)

    assert [item["current"] for item in response["anchors"]] == [False, True, False]
    assert normalize_site_id_hash("1" * 64) == site_id_hash
    assert normalize_site_id_hash(f"0x{'1' * 64}") == site_id_hash
