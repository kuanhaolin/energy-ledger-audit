import asyncio
import json
import sqlite3
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain.models import AnchorRecord, TrustedCertificate


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Repository:
    """Minimal state store. It deliberately does not retain the submitted Manifest."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS trusted_certificates (
                    fingerprint TEXT PRIMARY KEY,
                    signer_id TEXT NOT NULL,
                    site_id_hash TEXT NOT NULL,
                    pem TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS anchors (
                    anchor_id TEXT PRIMARY KEY,
                    manifest_id TEXT NOT NULL,
                    site_id_hash TEXT NOT NULL,
                    business_date TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    daily_root TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL UNIQUE,
                    prev_manifest_hash TEXT,
                    amends_manifest_hash TEXT,
                    status TEXT NOT NULL,
                    tx_hash TEXT,
                    block_number INTEGER,
                    block_hash TEXT,
                    transaction_index INTEGER,
                    log_index INTEGER,
                    submitter TEXT,
                    anchored_at TEXT,
                    receipt_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(site_id_hash, sequence)
                );
                CREATE INDEX IF NOT EXISTS anchors_site_status_sequence
                    ON anchors(site_id_hash, status, sequence DESC);
                CREATE INDEX IF NOT EXISTS anchors_site_date_sequence
                    ON anchors(site_id_hash, business_date, sequence ASC);
                """
            )

    async def upsert_certificate(self, certificate: TrustedCertificate) -> None:
        await asyncio.to_thread(self._upsert_certificate_sync, certificate)

    def _upsert_certificate_sync(self, certificate: TrustedCertificate) -> None:
        now = utc_now()
        with self._connect() as database:
            database.execute(
                """
                INSERT INTO trusted_certificates
                    (fingerprint, signer_id, site_id_hash, pem, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    signer_id=excluded.signer_id,
                    site_id_hash=excluded.site_id_hash,
                    pem=excluded.pem,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    certificate.fingerprint.lower(),
                    certificate.signer_id,
                    certificate.site_id_hash,
                    certificate.pem,
                    int(certificate.enabled),
                    now,
                    now,
                ),
            )

    async def get_certificate(self, fingerprint: str) -> TrustedCertificate | None:
        return await asyncio.to_thread(self._get_certificate_sync, fingerprint)

    def _get_certificate_sync(self, fingerprint: str) -> TrustedCertificate | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT * FROM trusted_certificates WHERE fingerprint = ?",
                (fingerprint.lower(),),
            ).fetchone()
        if row is None:
            return None
        return TrustedCertificate(
            fingerprint=row["fingerprint"],
            signer_id=row["signer_id"],
            site_id_hash=row["site_id_hash"],
            pem=row["pem"],
            enabled=bool(row["enabled"]),
        )

    @staticmethod
    def _anchor_from_row(row: sqlite3.Row | None) -> AnchorRecord | None:
        if row is None:
            return None
        values = dict(row)
        values["receipt"] = json.loads(values.pop("receipt_json")) if values["receipt_json"] else None
        allowed = {field.name for field in fields(AnchorRecord)}
        return AnchorRecord(**{key: value for key, value in values.items() if key in allowed})

    async def create_anchor(self, anchor: AnchorRecord) -> AnchorRecord:
        return await asyncio.to_thread(self._create_anchor_sync, anchor)

    def _create_anchor_sync(self, anchor: AnchorRecord) -> AnchorRecord:
        now = utc_now()
        with self._connect() as database:
            database.execute(
                """
                INSERT INTO anchors (
                    anchor_id, manifest_id, site_id_hash, business_date, sequence,
                    daily_root, manifest_hash, prev_manifest_hash, amends_manifest_hash,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    anchor.anchor_id,
                    anchor.manifest_id,
                    anchor.site_id_hash,
                    anchor.business_date,
                    anchor.sequence,
                    anchor.daily_root,
                    anchor.manifest_hash,
                    anchor.prev_manifest_hash,
                    anchor.amends_manifest_hash,
                    anchor.status,
                    now,
                    now,
                ),
            )
            row = database.execute(
                "SELECT * FROM anchors WHERE anchor_id = ?", (anchor.anchor_id,)
            ).fetchone()
        created = self._anchor_from_row(row)
        assert created is not None
        return created

    async def get_anchor(self, anchor_id: str) -> AnchorRecord | None:
        return await asyncio.to_thread(self._get_anchor_sync, anchor_id)

    def _get_anchor_sync(self, anchor_id: str) -> AnchorRecord | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT * FROM anchors WHERE anchor_id = ?", (anchor_id,)
            ).fetchone()
        return self._anchor_from_row(row)

    async def get_by_manifest_hash(self, manifest_hash: str) -> AnchorRecord | None:
        return await asyncio.to_thread(self._get_by_manifest_hash_sync, manifest_hash)

    def _get_by_manifest_hash_sync(self, manifest_hash: str) -> AnchorRecord | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT * FROM anchors WHERE manifest_hash = ?", (manifest_hash.lower(),)
            ).fetchone()
        return self._anchor_from_row(row)

    async def latest_finalized(self, site_id_hash: str) -> AnchorRecord | None:
        return await asyncio.to_thread(self._latest_finalized_sync, site_id_hash)

    async def list_by_site_date(
        self, site_id_hash: str, business_date: str
    ) -> list[AnchorRecord]:
        return await asyncio.to_thread(
            self._list_by_site_date_sync,
            site_id_hash,
            business_date,
        )

    def _list_by_site_date_sync(
        self, site_id_hash: str, business_date: str
    ) -> list[AnchorRecord]:
        with self._connect() as database:
            rows = database.execute(
                """
                SELECT * FROM anchors
                WHERE site_id_hash = ? AND business_date = ?
                ORDER BY sequence ASC
                """,
                (site_id_hash, business_date),
            ).fetchall()
        return [anchor for row in rows if (anchor := self._anchor_from_row(row)) is not None]

    def _latest_finalized_sync(self, site_id_hash: str) -> AnchorRecord | None:
        with self._connect() as database:
            row = database.execute(
                """
                SELECT * FROM anchors
                WHERE site_id_hash = ? AND status = 'FINALIZED'
                ORDER BY sequence DESC LIMIT 1
                """,
                (site_id_hash,),
            ).fetchone()
        return self._anchor_from_row(row)

    async def claim_next(self) -> AnchorRecord | None:
        return await asyncio.to_thread(self._claim_next_sync)

    async def queue_validated(self, anchor_id: str) -> AnchorRecord:
        return await asyncio.to_thread(self._queue_validated_sync, anchor_id)

    def _queue_validated_sync(self, anchor_id: str) -> AnchorRecord:
        with self._connect() as database:
            database.execute(
                """
                UPDATE anchors SET status='QUEUED', updated_at=?
                WHERE anchor_id=? AND status='VALIDATED'
                """,
                (utc_now(), anchor_id),
            )
        anchor = self._get_anchor_sync(anchor_id)
        assert anchor is not None
        return anchor

    def _claim_next_sync(self) -> AnchorRecord | None:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT * FROM anchors WHERE status = 'QUEUED' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                database.commit()
                return None
            database.execute(
                "UPDATE anchors SET status = 'SUBMITTING', updated_at = ? WHERE anchor_id = ?",
                (utc_now(), row["anchor_id"]),
            )
            database.commit()
        return self._get_anchor_sync(row["anchor_id"])

    async def mark_finalized(self, anchor_id: str, chain: dict[str, Any], receipt: dict[str, Any]) -> None:
        await asyncio.to_thread(self._mark_finalized_sync, anchor_id, chain, receipt)

    def _mark_finalized_sync(
        self, anchor_id: str, chain: dict[str, Any], receipt: dict[str, Any]
    ) -> None:
        with self._connect() as database:
            database.execute(
                """
                UPDATE anchors SET status='FINALIZED', tx_hash=?, block_number=?, block_hash=?,
                    transaction_index=?, log_index=?, submitter=?, anchored_at=?, receipt_json=?,
                    error_code=NULL, error_message=NULL, updated_at=?
                WHERE anchor_id=?
                """,
                (
                    chain["txHash"],
                    chain["blockNumber"],
                    chain["blockHash"],
                    chain.get("transactionIndex"),
                    chain.get("logIndex"),
                    chain.get("submitter"),
                    chain["anchoredAt"],
                    json.dumps(receipt, ensure_ascii=False, separators=(",", ":")),
                    utc_now(),
                    anchor_id,
                ),
            )

    async def mark_failed(self, anchor_id: str, code: str, message: str) -> None:
        await asyncio.to_thread(self._mark_failed_sync, anchor_id, code, message)

    def _mark_failed_sync(self, anchor_id: str, code: str, message: str) -> None:
        with self._connect() as database:
            database.execute(
                """
                UPDATE anchors SET status='FAILED', error_code=?, error_message=?, updated_at=?
                WHERE anchor_id=?
                """,
                (code, message, utc_now(), anchor_id),
            )
