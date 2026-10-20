import hashlib
import json
from pathlib import Path
from typing import Any

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker

from app.domain.errors import hash_mismatch, schema_invalid

SCHEMA_DIRECTORY = Path(__file__).resolve().parent.parent / "schemas"

def manifest_body(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in {"manifestHash", "signatures"}
    }

def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return rfc8785.dumps(manifest_body(manifest))

def calculate_manifest_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()

class ManifestSchemaRegistry:
    def __init__(self, schema_directory: Path = SCHEMA_DIRECTORY) -> None:
        self.schema_directory = schema_directory

    def validate(self, manifest: dict[str, Any]) -> None:
        version = manifest.get("schemaVersion")
        if not isinstance(version, str):
            raise schema_invalid("schemaVersion is required")

        path = self.schema_directory / f"manifest-{version}.json"
        if not path.exists():
            raise schema_invalid(
                f"Unsupported schemaVersion: {version}",
                {"schemaVersion": version},
            )

        schema = json.loads(path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(
            validator.iter_errors(manifest),
            key=lambda item: "/".join(str(part) for part in item.path),
        )
        if not errors:
            self._validate_category_semantics(manifest)
            return

        first = errors[0]
        field_path = ".".join(str(part) for part in first.absolute_path) or "$"
        raise schema_invalid(
            "Daily Manifest does not match its JSON Schema",
            {"path": field_path, "reason": first.message, "schemaVersion": version},
        )

    @staticmethod
    def _validate_category_semantics(manifest: dict[str, Any]) -> None:
        required = {"OPERATION", "EVENT", "TRADE", "STRATEGY", "AUDIT", "HEALTH", "METER"}
        received = [item.get("category") for item in manifest["categories"]]
        if set(received) != required or len(received) != len(set(received)):
            raise schema_invalid(
                "categories must contain each required category exactly once",
                {"required": sorted(required), "received": received},
            )


def validate_manifest_hash(manifest: dict[str, Any]) -> str:
    calculated = calculate_manifest_hash(manifest)
    supplied = str(manifest["manifestHash"]).lower()
    if calculated != supplied:
        raise hash_mismatch(supplied, calculated)
    return calculated
