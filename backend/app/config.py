from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_name: str = "Energy Ledger Notary"
    app_env: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api/v1"
    frontend_origin: str = "http://localhost:5173"

    chain_key: str = "ethereum-sepolia"
    chain_name: str = "Ethereum Sepolia"
    chain_id: int = 11155111
    chain_consensus: str = "Proof of Stake"
    rpc_url: str = "https://rpc.sepolia.org"
    block_explorer_url: str = "https://sepolia.etherscan.io"
    chain_confirmations: int = Field(default=2, ge=1)
    chain_receipt_timeout_seconds: int = Field(default=180, ge=30)
    anchor_mode: Literal["validate-only", "live"] = "validate-only"

    anchor_contract_address: str = ""
    anchor_deployment_block: str = ""
    anchor_abi_path: str = "app/contracts/EnergyLedgerAnchor.json"
    signer_private_key: str = ""
    notary_private_key_path: str = ""
    notary_private_key_password: str = ""
    notary_certificate_path: str = ""
    notary_org: str = "National Central University"
    notary_service: str = "NCU Energy Ledger Notary"

    database_path: str = "data/notary.db"

    mtls_required: bool = False
    mtls_fingerprint_header: str = "X-Client-Cert-Fingerprint"
    mtls_site_header: str = "X-Client-Site-Id-Hash"

    @field_validator("anchor_contract_address", "signer_private_key", mode="before")
    @classmethod
    def strip_secret_like_values(cls, value: object) -> str:
        return "" if value is None else str(value).strip()

    @property
    def contract_configured(self) -> bool:
        address = self.anchor_contract_address
        return len(address) == 42 and address.startswith("0x")

    @property
    def deployment_block(self) -> int | None:
        raw = self.anchor_deployment_block.strip()
        return int(raw) if raw else None

    @property
    def abi_path(self) -> Path:
        path = Path(self.anchor_abi_path)
        return path if path.is_absolute() else BACKEND_ROOT / path

    def resolve_backend_path(self, value: str) -> Path | None:
        if not value.strip():
            return None
        path = Path(value)
        return path if path.is_absolute() else BACKEND_ROOT / path

    @property
    def database_file(self) -> Path:
        path = Path(self.database_path)
        return path if path.is_absolute() else BACKEND_ROOT / path

    @property
    def notary_private_key_file(self) -> Path | None:
        return self.resolve_backend_path(self.notary_private_key_path)

    @property
    def notary_certificate_file(self) -> Path | None:
        return self.resolve_backend_path(self.notary_certificate_path)

    @property
    def live_chain_configured(self) -> bool:
        private_key = self.notary_private_key_file
        certificate = self.notary_certificate_file
        return all(
            (
                self.contract_configured,
                self.abi_path.exists(),
                bool(self.signer_private_key),
                private_key is not None and private_key.exists(),
                certificate is not None and certificate.exists(),
            )
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
