import re
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, Response, status

from app.blockchain.client import BlockchainClient
from app.config import get_settings
from app.domain.errors import (
    PUBLIC_CHAIN_FAILURE_MESSAGE,
    NotaryError,
    anchor_not_found,
    schema_invalid,
)
from app.domain.models import AnchorRecord
from app.services.anchor_service import AnchorService

router = APIRouter()


# SQLite/Worker use operational states that are useful inside the service.
# The API translates them to the status names required by SRS section 5.3.
EXTERNAL_STATUS_BY_INTERNAL = {
    "VALIDATED": "VERIFIED",
    "QUEUED": "VERIFIED",
    "SUBMITTING": "SUBMITTED",
    "FINALIZED": "FINALIZED",
    "FAILED": "REJECTED",
}


def anchor_response(anchor: AnchorRecord) -> dict[str, Any]:
    if anchor.status == "FINALIZED" and anchor.receipt is not None:
        return anchor.receipt
    external_status = EXTERNAL_STATUS_BY_INTERNAL[anchor.status]
    result: dict[str, Any] = {
        "anchorId": anchor.anchor_id,
        "manifestId": anchor.manifest_id,
        "manifestHash": anchor.manifest_hash,
        "dailyRoot": anchor.daily_root,
        "status": external_status,
        "receivedAt": anchor.created_at,
    }
    if anchor.tx_hash:
        result["chain"] = {
            "network": get_settings().chain_name,
            "chainId": get_settings().chain_id,
            "consensus": get_settings().chain_consensus,
            "contractAddress": get_settings().anchor_contract_address,
            "txHash": anchor.tx_hash,
            "blockNumber": anchor.block_number,
            "blockHash": anchor.block_hash,
            "logIndex": anchor.log_index,
        }
    if external_status == "REJECTED":
        result["error"] = {
            "code": "CHAIN_SUBMISSION_FAILED",
            "message": PUBLIC_CHAIN_FAILURE_MESSAGE,
        }
    return result


def anchor_accepted_response(anchor: AnchorRecord) -> dict[str, Any]:
    return {
        "anchorId": anchor.anchor_id,
        "manifestHash": anchor.manifest_hash,
        "status": "RECEIVED",
        "receivedAt": anchor.created_at,
    }


def anchor_status_response(anchor: AnchorRecord) -> dict[str, Any]:
    result: dict[str, Any] = {
        "anchorId": anchor.anchor_id,
        "manifestHash": anchor.manifest_hash,
        "status": EXTERNAL_STATUS_BY_INTERNAL[anchor.status],
        "receivedAt": anchor.created_at,
    }
    if anchor.status == "FAILED":
        result["error"] = {
            "code": "CHAIN_SUBMISSION_FAILED",
            "message": PUBLIC_CHAIN_FAILURE_MESSAGE,
        }
    return result


def normalize_site_id_hash(value: str) -> str:
    normalized = value.lower()
    if normalized.startswith("sha256:"):
        hex_value = normalized.removeprefix("sha256:")
    else:
        hex_value = normalized.removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{64}", hex_value):
        raise schema_invalid(
            "siteIdHash must contain a SHA-256 hex value",
            {"siteIdHash": value},
        )
    return f"sha256:{hex_value}"


def validate_business_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise schema_invalid(
            "businessDate must use YYYY-MM-DD format",
            {"businessDate": value},
        ) from error


def site_date_response(
    site_id_hash: str,
    business_date: str,
    anchors: list[AnchorRecord],
) -> dict[str, Any]:
    finalized = [anchor for anchor in anchors if anchor.status == "FINALIZED"]
    current_anchor = max(finalized, key=lambda item: item.sequence, default=None)
    return {
        "siteIdHash": site_id_hash,
        "businessDate": business_date,
        "anchors": [
            {
                "anchorId": anchor.anchor_id,
                "manifestId": anchor.manifest_id,
                "manifestType": ("AMENDMENT" if anchor.amends_manifest_hash else "DAILY"),
                "manifestHash": anchor.manifest_hash,
                "dailyRoot": anchor.daily_root,
                "sequence": anchor.sequence,
                "status": EXTERNAL_STATUS_BY_INTERNAL[anchor.status],
                "amendsManifestHash": anchor.amends_manifest_hash,
                "current": (
                    current_anchor is not None and anchor.anchor_id == current_anchor.anchor_id
                ),
                "receivedAt": anchor.created_at,
                "anchoredAt": anchor.anchored_at,
            }
            for anchor in anchors
        ],
    }


def validate_idempotency_key(idempotency_key: str | None, manifest: dict[str, Any]) -> None:
    if not idempotency_key:
        raise schema_invalid(
            "Idempotency-Key header is required",
            {"header": "Idempotency-Key"},
        )

    manifest_hash = manifest.get("manifestHash")
    if not isinstance(manifest_hash, str) or idempotency_key != manifest_hash:
        raise schema_invalid(
            "Idempotency-Key must equal manifestHash",
            {
                "header": "Idempotency-Key",
                "idempotencyKey": idempotency_key,
                "manifestHash": manifest_hash,
            },
        )


@router.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/chain/config", tags=["blockchain"])
async def chain_config() -> dict[str, object]:
    settings = get_settings()
    return {
        "key": settings.chain_key,
        "name": settings.chain_name,
        "chainId": settings.chain_id,
        "blockExplorerUrl": settings.block_explorer_url,
        "confirmations": settings.chain_confirmations,
        "anchorMode": settings.anchor_mode,
        "contractAddress": settings.anchor_contract_address or None,
        "deploymentBlock": settings.deployment_block,
        "contractConfigured": settings.contract_configured,
        "abiConfigured": settings.abi_path.exists(),
        "liveChainConfigured": settings.live_chain_configured,
    }


@router.get("/chain/status", tags=["blockchain"])
async def chain_status() -> dict[str, object]:
    settings = get_settings()
    try:
        return await BlockchainClient(settings).status()
    except Exception:  # noqa: BLE001 - Do not expose third-party RPC exceptions to callers.
        raise NotaryError(
            503,
            "CHAIN_UNAVAILABLE",
            f"Cannot connect to {settings.chain_name} RPC",
        ) from None


@router.post("/anchors", status_code=status.HTTP_202_ACCEPTED, tags=["anchors"])
async def create_anchor(
    request: Request,
    response: Response,
    body: dict[str, Any],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    settings = get_settings()
    manifest = body.get("manifest") if set(body) == {"manifest"} else body
    if not isinstance(manifest, dict):
        raise NotaryError(400, "SCHEMA_INVALID", "Request body must be a Daily Manifest object")

    validate_idempotency_key(idempotency_key, manifest)

    service = AnchorService(settings, request.app.state.repository)
    submission = await service.submit(
        manifest,
        request.headers.get(settings.mtls_fingerprint_header),
        request.headers.get(settings.mtls_site_header),
    )
    if submission.created:
        response.status_code = status.HTTP_202_ACCEPTED
        return anchor_accepted_response(submission.anchor)

    response.status_code = status.HTTP_200_OK
    return anchor_status_response(submission.anchor)


@router.get("/anchors", tags=["anchors"])
async def query_anchors(
    request: Request,
    manifest_hash: Annotated[str | None, Query(alias="manifestHash")] = None,
    site_id_hash: Annotated[str | None, Query(alias="siteIdHash")] = None,
    business_date: Annotated[str | None, Query(alias="businessDate")] = None,
) -> dict[str, Any]:
    if manifest_hash is not None:
        if site_id_hash is not None or business_date is not None:
            raise schema_invalid("Use manifestHash or siteIdHash with businessDate, not both")
        anchor = await request.app.state.repository.get_by_manifest_hash(
            manifest_hash.removeprefix("0x").lower()
        )
        if anchor is None:
            raise anchor_not_found()
        return anchor_response(anchor)

    if site_id_hash is None or business_date is None:
        raise schema_invalid("Provide manifestHash or both siteIdHash and businessDate")

    normalized_site_id_hash = normalize_site_id_hash(site_id_hash)
    normalized_business_date = validate_business_date(business_date)
    anchors = await request.app.state.repository.list_by_site_date(
        normalized_site_id_hash,
        normalized_business_date,
    )
    return site_date_response(
        normalized_site_id_hash,
        normalized_business_date,
        anchors,
    )


@router.get("/anchors/{anchor_id}", tags=["anchors"])
async def get_anchor(request: Request, anchor_id: str) -> dict[str, Any]:
    anchor = await request.app.state.repository.get_anchor(anchor_id)
    if anchor is None:
        raise anchor_not_found()
    return anchor_response(anchor)
