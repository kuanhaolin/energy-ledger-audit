from typing import Any

from app.domain.errors import amends_invalid, chain_broken
from app.domain.models import AnchorRecord
from app.repository import Repository


def amendment_hash(manifest: dict[str, Any]) -> str | None:
    amends = manifest.get("amends")
    if isinstance(amends, str):
        return amends.lower()
    if isinstance(amends, dict):
        value = amends.get("manifestHash")
        return value.lower() if isinstance(value, str) else None
    return None


class LinkageValidator:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    async def validate(self, manifest: dict[str, Any]) -> str | None:
        latest = await self.repository.latest_finalized(manifest["siteIdHash"])
        expected_sequence = 1 if latest is None else latest.sequence + 1
        expected_hash = None if latest is None else latest.manifest_hash
        received_hash = manifest.get("prevManifestHash")
        if received_hash is not None:
            received_hash = received_hash.lower()

        if manifest["sequence"] != expected_sequence or received_hash != expected_hash:
            raise chain_broken(expected_hash, expected_sequence)

        amended_hash = amendment_hash(manifest)
        if manifest["manifestType"] == "DAILY":
            if amended_hash is not None:
                raise amends_invalid("A DAILY Manifest must not contain amends", amended_hash)
            return None

        if amended_hash is None:
            raise amends_invalid("An AMENDMENT Manifest must identify the original manifestHash")

        original = await self.repository.get_by_manifest_hash(amended_hash)
        if original is None or original.status != "FINALIZED":
            raise amends_invalid("The amended Manifest has not been finalized", amended_hash)
        if (
            original.site_id_hash != manifest["siteIdHash"]
            or original.business_date != manifest["businessDate"]
        ):
            raise amends_invalid(
                "The amended Manifest must have the same siteIdHash and businessDate",
                amended_hash,
            )
        return amended_hash


def same_anchor(anchor: AnchorRecord, manifest: dict[str, Any]) -> bool:
    return anchor.manifest_hash == manifest["manifestHash"].lower()
