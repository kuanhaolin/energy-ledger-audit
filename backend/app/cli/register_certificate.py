import argparse
import asyncio
from pathlib import Path

from app.config import get_settings
from app.domain.models import TrustedCertificate
from app.repository import Repository
from app.services.signature_verifier import certificate_fingerprint


async def register(args: argparse.Namespace) -> None:
    pem = Path(args.pem).read_text(encoding="utf-8")
    fingerprint = certificate_fingerprint(pem)
    repository = Repository(get_settings().database_file)
    await repository.initialize()
    await repository.upsert_certificate(
        TrustedCertificate(
            fingerprint=fingerprint,
            signer_id=args.signer_id,
            site_id_hash=args.site_id_hash,
            pem=pem,
            enabled=True,
        )
    )
    print(f"registered {fingerprint} for {args.site_id_hash}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Register a trusted EMS ES256 certificate")
    parser.add_argument("--pem", required=True, help="Path to the EMS X.509 PEM certificate")
    parser.add_argument("--signer-id", required=True, help="Expected Manifest signerId")
    parser.add_argument("--site-id-hash", required=True, help="Expected sha256:<hex> siteIdHash")
    asyncio.run(register(parser.parse_args()))


if __name__ == "__main__":
    main()
