import sqlite3
import uuid
from typing import Any

from app.config import Settings
from app.domain.errors import chain_broken, chain_not_configured, mtls_invalid
from app.domain.models import AnchorRecord, AnchorSubmissionResult
from app.repository import Repository
from app.services.linkage import LinkageValidator
from app.services.manifest_validation import ManifestSchemaRegistry, validate_manifest_hash
from app.services.signature_verifier import ManifestSignatureVerifier


class AnchorService:
    def __init__(self, settings: Settings, repository: Repository) -> None:
        self.settings = settings
        self.repository = repository
        self.schemas = ManifestSchemaRegistry()
        self.signatures = ManifestSignatureVerifier(repository)
        self.linkage = LinkageValidator(repository)

    async def submit(
        self,
        manifest: dict[str, Any],
        mtls_fingerprint: str | None,
        mtls_site_id_hash: str | None,
    ) -> AnchorSubmissionResult:
        self.schemas.validate(manifest)
        manifest_hash = validate_manifest_hash(manifest)

        if self.settings.mtls_required and (not mtls_fingerprint or not mtls_site_id_hash):
            raise mtls_invalid("A trusted proxy must supply the mTLS certificate identity")

        await self.signatures.verify_platform_signature(
            manifest,
            mtls_fingerprint=mtls_fingerprint,
            mtls_site_id_hash=mtls_site_id_hash,
        )

        existing = await self.repository.get_by_manifest_hash(manifest_hash)
        if existing is not None:
            if self.settings.anchor_mode == "live" and existing.status == "VALIDATED":
                await self.linkage.validate(manifest)
                if not self.settings.live_chain_configured:
                    raise chain_not_configured()
                queued = await self.repository.queue_validated(existing.anchor_id)
                return AnchorSubmissionResult(anchor=queued, created=False)
            return AnchorSubmissionResult(anchor=existing, created=False)

        amended_hash = await self.linkage.validate(manifest)

        if self.settings.anchor_mode == "live" and not self.settings.live_chain_configured:
            raise chain_not_configured()

        anchor = AnchorRecord(
            anchor_id=f"anc_{uuid.uuid4().hex}",
            manifest_id=manifest["manifestId"],
            site_id_hash=manifest["siteIdHash"],
            business_date=manifest["businessDate"],
            sequence=manifest["sequence"],
            daily_root=manifest["dailyRoot"].lower(),
            manifest_hash=manifest_hash,
            prev_manifest_hash=(manifest.get("prevManifestHash") or None),
            amends_manifest_hash=amended_hash,
            status="QUEUED" if self.settings.anchor_mode == "live" else "VALIDATED",
        )
        try:
            created = await self.repository.create_anchor(anchor)
            return AnchorSubmissionResult(anchor=created, created=True)
        except sqlite3.IntegrityError:
            duplicate = await self.repository.get_by_manifest_hash(manifest_hash)
            if duplicate is not None:
                return AnchorSubmissionResult(anchor=duplicate, created=False)
            latest = await self.repository.latest_finalized(manifest["siteIdHash"])
            raise chain_broken(
                latest.manifest_hash if latest else None,
                latest.sequence + 1 if latest else 1,
            ) from None
