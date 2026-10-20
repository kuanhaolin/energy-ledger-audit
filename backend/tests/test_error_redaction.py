import asyncio
from types import SimpleNamespace

import pytest

from app.api import routes
from app.domain.errors import PUBLIC_CHAIN_FAILURE_MESSAGE, NotaryError
from app.domain.models import AnchorRecord
from app.worker import AnchorWorker

FAKE_SECRET = "FAKE_RPC_KEY_DO_NOT_USE_123456789"


def failed_anchor() -> AnchorRecord:
    return AnchorRecord(
        anchor_id="anc_failed",
        manifest_id="site:2026-09-03",
        site_id_hash=f"sha256:{'1' * 64}",
        business_date="2026-09-03",
        sequence=1,
        daily_root="2" * 64,
        manifest_hash="3" * 64,
        prev_manifest_hash=None,
        amends_manifest_hash=None,
        status="FAILED",
        error_code="CHAIN_SUBMISSION_FAILED",
        error_message=f"403, url='https://example.invalid/v2/{FAKE_SECRET}'",
    )


def test_old_database_error_is_not_exposed_by_anchor_queries() -> None:
    anchor = failed_anchor()
    for response in (routes.anchor_response(anchor), routes.anchor_status_response(anchor)):
        assert response["error"] == {
            "code": "CHAIN_SUBMISSION_FAILED",
            "message": PUBLIC_CHAIN_FAILURE_MESSAGE,
        }
        assert FAKE_SECRET not in str(response)


async def test_chain_status_does_not_expose_rpc_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenClient:
        async def status(self):
            raise RuntimeError(f"403, url='https://example.invalid/v2/{FAKE_SECRET}'")

    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(chain_name="Sepolia"))
    monkeypatch.setattr(routes, "BlockchainClient", lambda _settings: BrokenClient())

    with pytest.raises(NotaryError) as caught:
        await routes.chain_status()

    assert caught.value.code == "CHAIN_UNAVAILABLE"
    assert caught.value.details == {}
    assert FAKE_SECRET not in caught.value.message


async def test_worker_stores_only_public_failure_message() -> None:
    worker = object.__new__(AnchorWorker)
    worker.settings = SimpleNamespace(anchor_mode="live")
    worker._stop = asyncio.Event()
    stored: list[tuple[str, str, str]] = []

    class RepositoryStub:
        async def claim_next(self):
            return SimpleNamespace(anchor_id="anc_failed")

        async def mark_failed(self, anchor_id, code, message):
            stored.append((anchor_id, code, message))
            worker.stop()

    class BlockchainStub:
        async def submit_anchor(self, _anchor):
            raise RuntimeError(f"403, url='https://example.invalid/v2/{FAKE_SECRET}'")

    worker.repository = RepositoryStub()
    worker.blockchain = BlockchainStub()

    await worker.run()

    assert stored == [("anc_failed", "CHAIN_SUBMISSION_FAILED", PUBLIC_CHAIN_FAILURE_MESSAGE)]
    assert FAKE_SECRET not in str(stored)
