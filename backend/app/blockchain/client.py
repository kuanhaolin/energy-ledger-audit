import asyncio
import json
from datetime import date
from typing import Any

from eth_account import Account
from web3 import AsyncWeb3, Web3
from web3.providers.rpc import AsyncHTTPProvider

from app.config import Settings
from app.domain.models import AnchorRecord
from app.services.receipt_signer import block_time_iso


class BlockchainConfigurationError(RuntimeError):
    """Raised when contract settings are missing or invalid."""


class BlockchainClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.web3 = AsyncWeb3(AsyncHTTPProvider(settings.rpc_url))

    async def status(self) -> dict[str, Any]:
        connected = await self.web3.is_connected()
        rpc_chain_id: int | None = None
        contract_has_code: bool | None = None

        if connected:
            rpc_chain_id = await self.web3.eth.chain_id
            if self.settings.contract_configured:
                address = Web3.to_checksum_address(self.settings.anchor_contract_address)
                contract_has_code = len(await self.web3.eth.get_code(address)) > 0

        return {
            "connected": connected,
            "expectedChainId": self.settings.chain_id,
            "rpcChainId": rpc_chain_id,
            "chainIdMatches": connected and rpc_chain_id == self.settings.chain_id,
            "contractConfigured": self.settings.contract_configured,
            "contractHasCode": contract_has_code,
        }

    def contract(self):
        if not self.settings.contract_configured:
            raise BlockchainConfigurationError(
                "ANCHOR_CONTRACT_ADDRESS 尚未設定，請在合約部署後填入 .env。"
            )
        if not self.settings.abi_path.exists():
            raise BlockchainConfigurationError(f"找不到合約 ABI：{self.settings.abi_path}")

        artifact = json.loads(self.settings.abi_path.read_text(encoding="utf-8"))
        abi = artifact["abi"] if isinstance(artifact, dict) and "abi" in artifact else artifact
        address = Web3.to_checksum_address(self.settings.anchor_contract_address)
        return self.web3.eth.contract(address=address, abi=abi)

    @staticmethod
    def _bytes32(value: str | None) -> bytes:
        if value is None:
            return bytes(32)
        normalized = value.removeprefix("sha256:").removeprefix("0x")
        raw = bytes.fromhex(normalized)
        if len(raw) != 32:
            raise ValueError("Expected a 32-byte hexadecimal value")
        return raw

    @staticmethod
    def _business_date(value: str) -> int:
        parsed = date.fromisoformat(value)
        return parsed.year * 10000 + parsed.month * 100 + parsed.day

    @staticmethod
    def _verified_event_log_index(logs: list[Any], expected: dict[str, Any]) -> int:
        if len(logs) != 1:
            raise RuntimeError(f"Expected one AnchorRecorded event, received {len(logs)}")

        try:
            event_args = dict(logs[0]["args"])
            log_index = logs[0]["logIndex"]
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("AnchorRecorded event is incomplete") from error

        for key, value in expected.items():
            if key not in event_args:
                raise RuntimeError(f"AnchorRecorded.{key} is missing")
            if event_args[key] != value:
                raise RuntimeError(f"AnchorRecorded.{key} does not match submitted value")
        return log_index

    def _anchor_arguments(self, anchor_function: dict[str, Any], anchor: AnchorRecord, sender: str):
        values = {
            "siteIdHash": self._bytes32(anchor.site_id_hash),
            "businessDate": self._business_date(anchor.business_date),
            "sequence": anchor.sequence,
            "dailyRoot": self._bytes32(anchor.daily_root),
            "manifestHash": self._bytes32(anchor.manifest_hash),
            "prevManifestHash": self._bytes32(anchor.prev_manifest_hash),
            "amendsManifestHash": self._bytes32(anchor.amends_manifest_hash),
            "submitter": sender,
            "anchoredAt": 0,
        }
        inputs = anchor_function.get("inputs", [])
        if len(inputs) == 1 and inputs[0].get("type", "").startswith("tuple"):
            components = inputs[0].get("components", [])
            return (tuple(values[item["name"]] for item in components),)
        return tuple(values[item["name"]] for item in inputs)

    async def submit_anchor(self, anchor: AnchorRecord) -> dict[str, Any]:
        connected = await self.web3.is_connected()
        if not connected:
            raise BlockchainConfigurationError(f"Cannot connect to {self.settings.chain_name} RPC")
        rpc_chain_id = await self.web3.eth.chain_id
        if rpc_chain_id != self.settings.chain_id:
            raise BlockchainConfigurationError(
                f"RPC chainId {rpc_chain_id} does not match configured {self.settings.chain_id}"
            )

        contract = self.contract()
        anchor_abi = next(
            item
            for item in contract.abi
            if item.get("type") == "function" and item.get("name") == "anchor"
        )
        account = Account.from_key(self.settings.signer_private_key)
        arguments = self._anchor_arguments(anchor_abi, anchor, account.address)
        function = contract.functions.anchor(*arguments)
        transaction = await function.build_transaction(
            {
                "from": account.address,
                "chainId": self.settings.chain_id,
                "nonce": await self.web3.eth.get_transaction_count(account.address, "pending"),
            }
        )
        signed = account.sign_transaction(transaction)
        tx_hash = await self.web3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = await self.web3.eth.wait_for_transaction_receipt(
            tx_hash,
            timeout=self.settings.chain_receipt_timeout_seconds,
            poll_latency=2,
        )
        if receipt["status"] != 1:
            raise RuntimeError(f"anchor transaction reverted: {tx_hash.hex()}")

        target_block = receipt["blockNumber"] + self.settings.chain_confirmations - 1
        while await self.web3.eth.block_number < target_block:
            await asyncio.sleep(2)

        block = await self.web3.eth.get_block(receipt["blockNumber"])
        logs = contract.events.AnchorRecorded().process_receipt(receipt)
        expected = {
            "siteIdHash": self._bytes32(anchor.site_id_hash),
            "businessDate": self._business_date(anchor.business_date),
            "sequence": anchor.sequence,
            "dailyRoot": self._bytes32(anchor.daily_root),
            "manifestHash": self._bytes32(anchor.manifest_hash),
            "submitter": account.address,
            "anchoredAt": block["timestamp"],
        }
        log_index = self._verified_event_log_index(logs, expected)

        block_timestamp = block_time_iso(block["timestamp"])
        return {
            "txHash": f"0x{tx_hash.hex().removeprefix('0x')}",
            "blockNumber": receipt["blockNumber"],
            "blockHash": f"0x{receipt['blockHash'].hex().removeprefix('0x')}",
            "blockTimestamp": block_timestamp,
            "transactionIndex": receipt["transactionIndex"],
            "logIndex": log_index,
            "submitter": account.address,
            "anchoredAt": block_timestamp,
        }
