import argparse
import asyncio
import re
import uuid
from datetime import date

from app.config import get_settings
from app.domain.models import AnchorRecord
from app.repository import Repository

HEX32 = re.compile(r"^[0-9a-f]{64}$")
SITE_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


def hex32(value: str) -> str:
    normalized = value.lower().removeprefix("0x")
    if not HEX32.fullmatch(normalized):
        raise argparse.ArgumentTypeError("must be exactly 32 bytes of hexadecimal")
    return normalized


def site_hash(value: str) -> str:
    normalized = value.lower()
    if not SITE_HASH.fullmatch(normalized):
        raise argparse.ArgumentTypeError("must use sha256:<64 lowercase hex> format")
    return normalized


def business_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD format") from error
    return value


async def import_checkpoint(args: argparse.Namespace) -> None:
    repository = Repository(get_settings().database_file)
    await repository.initialize()
    existing = await repository.get_by_manifest_hash(args.manifest_hash)
    if existing is not None:
        print(f"already exists as {existing.anchor_id}")
        return
    anchor = await repository.create_anchor(
        AnchorRecord(
            anchor_id=f"anc_checkpoint_{uuid.uuid4().hex}",
            manifest_id=args.manifest_id,
            site_id_hash=args.site_id_hash,
            business_date=args.business_date,
            sequence=args.sequence,
            daily_root=args.daily_root,
            manifest_hash=args.manifest_hash,
            prev_manifest_hash=args.prev_manifest_hash,
            amends_manifest_hash=None,
            status="FINALIZED",
        )
    )
    print(f"imported finalized checkpoint {anchor.anchor_id}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import one previously verified on-chain Anchor as the local chain checkpoint"
    )
    parser.add_argument("--manifest-id", required=True)
    parser.add_argument("--site-id-hash", required=True, type=site_hash)
    parser.add_argument("--business-date", required=True, type=business_date)
    parser.add_argument("--sequence", required=True, type=int)
    parser.add_argument("--daily-root", required=True, type=hex32)
    parser.add_argument("--manifest-hash", required=True, type=hex32)
    parser.add_argument("--prev-manifest-hash", type=hex32)
    args = parser.parse_args()
    if args.sequence < 1:
        parser.error("--sequence must be at least 1")
    asyncio.run(import_checkpoint(args))


if __name__ == "__main__":
    main()
