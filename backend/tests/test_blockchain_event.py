import pytest

from app.blockchain.client import BlockchainClient

EXPECTED = {
    "siteIdHash": bytes.fromhex("11" * 32),
    "businessDate": 20260903,
    "sequence": 1,
    "dailyRoot": bytes.fromhex("22" * 32),
    "manifestHash": bytes.fromhex("33" * 32),
    "submitter": "0x0000000000000000000000000000000000000001",
    "anchoredAt": 1780000000,
}


def event(**changes):
    return {"args": {**EXPECTED, **changes}, "logIndex": 0}


def test_matching_anchor_event_returns_log_index() -> None:
    assert BlockchainClient._verified_event_log_index([event()], EXPECTED) == 0


@pytest.mark.parametrize(
    "logs, message",
    [
        ([], "Expected one AnchorRecorded event"),
        ([event(), event()], "Expected one AnchorRecorded event"),
        (
            [
                {
                    "args": {key: value for key, value in EXPECTED.items() if key != "dailyRoot"},
                    "logIndex": 0,
                }
            ],
            "AnchorRecorded.dailyRoot is missing",
        ),
        (
            [event(manifestHash=bytes.fromhex("44" * 32))],
            "AnchorRecorded.manifestHash does not match",
        ),
        ([{"args": EXPECTED}], "AnchorRecorded event is incomplete"),
    ],
)
def test_anchor_event_must_be_complete_and_match(logs, message) -> None:
    with pytest.raises(RuntimeError, match=message):
        BlockchainClient._verified_event_log_index(logs, EXPECTED)
