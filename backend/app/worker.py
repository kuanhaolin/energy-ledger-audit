import asyncio
import logging

from app.blockchain.client import BlockchainClient
from app.config import Settings
from app.domain.errors import PUBLIC_CHAIN_FAILURE_MESSAGE
from app.repository import Repository
from app.services.receipt_signer import ReceiptSigner

logger = logging.getLogger(__name__)


class AnchorWorker:
    def __init__(self, settings: Settings, repository: Repository) -> None:
        self.settings = settings
        self.repository = repository
        self.blockchain = BlockchainClient(settings)
        self.receipts = ReceiptSigner(settings)
        if settings.anchor_mode == "live":
            self.receipts.validate_configuration()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        if self.settings.anchor_mode != "live":
            return
        while not self._stop.is_set():
            anchor = await self.repository.claim_next()
            if anchor is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=2)
                except TimeoutError:
                    pass
                continue
            try:
                chain = await self.blockchain.submit_anchor(anchor)
                receipt = self.receipts.create(anchor, chain)
                await self.repository.mark_finalized(anchor.anchor_id, chain, receipt)
            except Exception as error:  # noqa: BLE001 - Persist a safe failure for any worker error.
                logger.error("Failed to anchor %s (%s)", anchor.anchor_id, type(error).__name__)
                await self.repository.mark_failed(
                    anchor.anchor_id,
                    "CHAIN_SUBMISSION_FAILED",
                    PUBLIC_CHAIN_FAILURE_MESSAGE,
                )
