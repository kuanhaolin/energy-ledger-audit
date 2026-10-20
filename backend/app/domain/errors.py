from typing import Any

PUBLIC_CHAIN_FAILURE_MESSAGE = "Blockchain submission failed. Contact the service operator."


class NotaryError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def schema_invalid(message: str, details: dict[str, Any] | None = None) -> NotaryError:
    return NotaryError(400, "SCHEMA_INVALID", message, details)


def hash_mismatch(expected: str, actual: str) -> NotaryError:
    return NotaryError(
        422,
        "MANIFEST_HASH_MISMATCH",
        "Calculated manifestHash does not match supplied value",
        {"expected": expected, "actual": actual},
    )


def cert_unknown(fingerprint: str) -> NotaryError:
    return NotaryError(
        401,
        "CERT_UNKNOWN",
        "No trusted EMS certificate matches certFingerprint",
        {"certFingerprint": fingerprint},
    )


def signature_invalid(message: str, details: dict[str, Any] | None = None) -> NotaryError:
    return NotaryError(401, "SIGNATURE_INVALID", message, details)


def chain_broken(expected_hash: str | None, expected_sequence: int) -> NotaryError:
    return NotaryError(
        409,
        "CHAIN_BROKEN",
        "prevManifestHash or sequence does not match last anchored manifest",
        {
            "expectedPrevManifestHash": expected_hash,
            "expectedSequence": expected_sequence,
        },
    )


def amends_invalid(message: str, manifest_hash: str | None = None) -> NotaryError:
    details = {"amendsManifestHash": manifest_hash} if manifest_hash else {}
    return NotaryError(422, "AMENDS_INVALID", message, details)


def mtls_invalid(message: str) -> NotaryError:
    return NotaryError(401, "MTLS_INVALID", message)


def chain_not_configured() -> NotaryError:
    return NotaryError(
        503,
        "CHAIN_NOT_CONFIGURED",
        "Live anchoring is enabled but contract, signer, or Receipt signing keys are missing",
    )


def anchor_not_found() -> NotaryError:
    return NotaryError(404, "ANCHOR_NOT_FOUND", "Anchor was not found")
