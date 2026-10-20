#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

CATEGORY_ORDER = [
    "OPERATION",
    "EVENT",
    "TRADE",
    "STRATEGY",
    "AUDIT",
    "HEALTH",
    "METER",
]
ZERO32 = bytes(32)

# NIST P-256 / secp256r1 parameters.
P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
Point = tuple[int, int] | None

def jcs(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False, # keep utf-8
        allow_nan=False,
    ).encode("utf-8")

def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()

def leaf_hash(record: dict[str, Any]) -> bytes:
    return sha256(b"\x00" + jcs(record))

def node_hash(left: bytes, right: bytes) -> bytes:
    return sha256(b"\x01" + left + right)

def merkle_root(nodes: list[bytes]) -> bytes:
    if not nodes:
        return ZERO32

    level = list(nodes)
    while len(level) > 1:
        next_level: list[bytes] = []
        for index in range(0, len(level), 2):
            if index + 1 == len(level):
                next_level.append(level[index])
            else:
                next_level.append(node_hash(level[index], level[index + 1]))
        level = next_level
    return level[0]

def walk_proof(start: bytes, path: list[dict[str, str]]) -> bytes:
    current = start
    for step in path:
        sibling = bytes.fromhex(step["sibling"])
        position = step["position"]
        if position == "left":
            current = node_hash(sibling, current)
        elif position == "right":
            current = node_hash(current, sibling)
        else:
            raise ValueError(f"Invalid proof position: {position}")
    return current

# manifest excluding manifestHash and signatures
def manifest_body(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in {"manifestHash", "signatures"}
    }

def certificate_der(pem: str) -> bytes:
    payload = "".join(
        line.strip()
        for line in pem.splitlines()
        if line and not line.startswith("-----")
    )
    return base64.b64decode(payload)


def inverse(value: int, modulus: int) -> int:
    return pow(value, -1, modulus)


def point_add(left: Point, right: Point) -> Point:
    if left is None:
        return right
    if right is None:
        return left

    x1, y1 = left
    x2, y2 = right

    if x1 == x2 and (y1 + y2) % P == 0:
        return None

    if left == right:
        if y1 == 0:
            return None
        slope = ((3 * x1 * x1 + A) * inverse(2 * y1, P)) % P
    else:
        slope = ((y2 - y1) * inverse((x2 - x1) % P, P)) % P

    x3 = (slope * slope - x1 - x2) % P
    y3 = (slope * (x1 - x3) - y1) % P
    return x3, y3


def scalar_multiply(scalar: int, point: Point) -> Point:
    result: Point = None
    addend = point
    while scalar:
        if scalar & 1:
            result = point_add(result, addend)
        addend = point_add(addend, addend)
        scalar >>= 1
    return result


def verify_es256_raw(
    message: bytes,
    signature_base64url: str,
    public_key_x: int,
    public_key_y: int,
) -> bool:
    padding = "=" * (-len(signature_base64url) % 4)
    signature = base64.urlsafe_b64decode(signature_base64url + padding)
    if len(signature) != 64:
        return False

    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if not (1 <= r < N and 1 <= s < N):
        return False

    public_key = (public_key_x, public_key_y)
    x, y = public_key
    # y² = x³ + A×x + B  mod P
    if (y * y - (x * x * x + A * x + 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B)) % P:
        return False

    digest = int.from_bytes(sha256(message), "big")
    w = inverse(s, N)
    u1 = (digest * w) % N
    u2 = (r * w) % N
    point = point_add(
        scalar_multiply(u1, (GX, GY)),
        scalar_multiply(u2, public_key),
    )
    return point is not None and point[0] % N == r


class Report:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, label: str, actual: Any, expected: Any = True) -> None:
        ok = actual == expected
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] {label}")
        if not ok:
            print(f"       expected: {expected}")
            print(f"       actual:   {actual}")
        self.passed += int(ok)
        self.failed += int(not ok)


def verify_fixture(fixture: dict[str, Any]) -> Report:
    report = Report()
    records = fixture["records"]
    expected = fixture["expected"]

    records_by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    records_by_id: dict[str, dict[str, Any]] = {}
    calculated_leaves: dict[str, bytes] = {}

    print("\n1. Record leaf hashes")
    for record in records:
        record_id = record["recordId"]
        records_by_id[record_id] = record
        records_by_category[record["category"]].append(record)
        calculated = leaf_hash(record)
        print(f"  {record_id}: {calculated.hex()}")
        calculated_leaves[record_id] = calculated
        report.check(
            record_id,
            calculated.hex(),
            expected["leafHashes"][record_id],
        )

    print("\n2. Category roots")
    calculated_category_roots: dict[str, bytes] = {}
    for category in CATEGORY_ORDER:
        category_records = sorted(
            records_by_category[category],
            key=lambda item: item["recordId"].encode("utf-8"),
        )
        root = merkle_root(
            [calculated_leaves[item["recordId"]] for item in category_records]
        )
        print(f"  {category}: {root.hex()}")
        calculated_category_roots[category] = root
        report.check(
            category,
            root.hex(),
            expected["categoryRoots"][category],
        )

    print("\n3. Daily root")
    daily_root = merkle_root(
        [calculated_category_roots[category] for category in CATEGORY_ORDER]
    )
    print(f"  dailyRoot: {daily_root.hex()}")
    report.check("dailyRoot", daily_root.hex(), expected["dailyRoot"])

    print("\n4. Manifest hash")
    manifest = fixture["manifest"]
    body = manifest_body(manifest)
    calculated_manifest_hash = sha256(jcs(body)).hex()
    print(f"  manifestHash: {calculated_manifest_hash}")
    report.check(
        "manifestHash",
        calculated_manifest_hash,
        expected["manifestHash"],
    )

    print("\n5. Certificate fingerprint and ES256 signature")
    certificate = fixture["certificate"]
    fingerprint = sha256(certificate_der(certificate["pem"])).hex()
    print(f"  certificateFingerprint: {fingerprint}")
    report.check(
        "certificate fingerprint",
        fingerprint,
        expected["certificateFingerprint"],
    )
    signature = manifest["signatures"][0]
    signature_ok = verify_es256_raw(
        jcs(body),
        signature["signature"],
        int(certificate["publicKeyX"], 16),
        int(certificate["publicKeyY"], 16),
    )
    print(f"  ES256 signature: {signature_ok}")
    report.check("ES256 signature", signature_ok)

    print("\n6. Inclusion proofs")
    for proof in fixture["proofs"]:
        record_id = proof["recordId"]
        leaf = leaf_hash(records_by_id[record_id])
        report.check(
            f"{record_id}: leafHash",
            leaf.hex(),
            proof["leafHash"],
        )
        category_root = walk_proof(leaf, proof["categoryPath"])
        report.check(
            f"{record_id}: categoryRoot",
            category_root.hex(),
            proof["categoryRoot"],
        )
        proof_daily_root = walk_proof(category_root, proof["dailyPath"])
        report.check(
            f"{record_id}: dailyRoot",
            proof_daily_root.hex(),
            proof["dailyRoot"],
        )

    return report


def main() -> int:
    fixture_path = Path(__file__).parent / "fixtures" / "appendix_c.json"
    if not fixture_path.is_file():
        print(f"本機測試資料不存在：{fixture_path}", file=sys.stderr)
        return 1
    with fixture_path.open(encoding="utf-8") as source:
        fixture = json.load(source)

    print("EMS SRS Appendix C verification")
    print(f"Fixture: {fixture_path}")
    report = verify_fixture(fixture)
    print(f"\nSummary: {report.passed} passed, {report.failed} failed")
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
