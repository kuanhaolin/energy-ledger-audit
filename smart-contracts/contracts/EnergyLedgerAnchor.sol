// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

contract EnergyLedgerAnchor {
    struct Anchor {
        bytes32 siteIdHash;          // 案場代碼雜湊
        uint32 businessDate;         // 時間日期
        uint32 sequence;             // 計數器
        bytes32 dailyRoot;           // 當日所有交易
        bytes32 manifestHash;        // Daily Manifest 雜湊
        bytes32 prevManifestHash;    // 前一份 Manifest 雜湊
        bytes32 amendsManifestHash;  // 補正對象，沒有則為 bytes32(0)
        address submitter;           // msg.sender
        uint64 anchoredAt;           // block.timestamp
    }

    address public admin;

    // 可以呼叫 anchor() 的帳號
    mapping(address => bool) public allowedSubmitters;

    // manifestHash => Anchor
    mapping(bytes32 => Anchor) private anchorsByManifestHash;

    // 判斷 manifestHash 是否已存在
    mapping(bytes32 => bool) public manifestExists;

    // 每個案場目前最新序號
    mapping(bytes32 => uint32) public latestSequence;

    // 每個案場最新的 manifestHash
    mapping(bytes32 => bytes32) public latestManifestHash;

    // siteIdHash => businessDate => manifestHash[]
    // 使用陣列是因為同一天可能有補正版
    mapping(bytes32 => mapping(uint32 => bytes32[]))
        private manifestsBySiteDate;

    event AnchorRecorded(
        bytes32 indexed siteIdHash,
        uint32 indexed businessDate,
        uint32 sequence,
        bytes32 indexed manifestHash,
        bytes32 dailyRoot,
        address submitter,
        uint64 anchoredAt
    );

    event SubmitterPermissionChanged(
        address indexed account,
        bool allowed
    );

    error Unauthorized();
    error InvalidInput();
    error ManifestAlreadyExists();
    error InvalidSequence(uint32 expected, uint32 received);
    error InvalidPreviousManifest(
        bytes32 expected,
        bytes32 received
    );
    error InvalidAmendment();

    modifier onlyAdmin() {
        if (msg.sender != admin) revert Unauthorized();
        _;
    }

    modifier onlySubmitter() {
        if (!allowedSubmitters[msg.sender]) revert Unauthorized();
        _;
    }

    constructor() {
        admin = msg.sender;
        allowedSubmitters[msg.sender] = true;
    }

    // 設定哪些帳號可以送資料上鏈
    function setSubmitter(
        address account,
        bool allowed
    ) external onlyAdmin {
        if (account == address(0)) revert InvalidInput();

        allowedSubmitters[account] = allowed;

        emit SubmitterPermissionChanged(account, allowed);
    }
    
    // 上鏈
    function anchor(
        bytes32 siteIdHash,
        uint32 businessDate,
        uint32 sequence,
        bytes32 dailyRoot,
        bytes32 manifestHash,
        bytes32 prevManifestHash,
        bytes32 amendsManifestHash
    ) external onlySubmitter {
        if (
            siteIdHash == bytes32(0) ||
            businessDate == 0 ||
            sequence == 0 ||
            dailyRoot == bytes32(0) ||
            manifestHash == bytes32(0)
        ) {
            revert InvalidInput();
        }

        if (manifestExists[manifestHash]) {
            revert ManifestAlreadyExists();
        }

        uint32 currentSequence = latestSequence[siteIdHash];
        uint32 expectedSequence = currentSequence + 1;

        if (sequence != expectedSequence) {
            revert InvalidSequence(expectedSequence, sequence);
        }

        bytes32 expectedPrevious =
            latestManifestHash[siteIdHash];

        if (prevManifestHash != expectedPrevious) {
            revert InvalidPreviousManifest(
                expectedPrevious,
                prevManifestHash
            );
        }

        // 如果是補正，原 Manifest 必須存在，
        // 而且必須屬於相同案場和相同營業日
        if (amendsManifestHash != bytes32(0)) {
            if (!manifestExists[amendsManifestHash]) {
                revert InvalidAmendment();
            }

            Anchor storage original =
                anchorsByManifestHash[amendsManifestHash];

            if (
                original.siteIdHash != siteIdHash ||
                original.businessDate != businessDate
            ) {
                revert InvalidAmendment();
            }
        }

        uint64 anchoredAt = uint64(block.timestamp);

        Anchor memory newAnchor = Anchor({
            siteIdHash: siteIdHash,
            businessDate: businessDate,
            sequence: sequence,
            dailyRoot: dailyRoot,
            manifestHash: manifestHash,
            prevManifestHash: prevManifestHash,
            amendsManifestHash: amendsManifestHash,
            submitter: msg.sender,
            anchoredAt: anchoredAt
        });

        anchorsByManifestHash[manifestHash] = newAnchor;
        manifestExists[manifestHash] = true;

        latestSequence[siteIdHash] = sequence;
        latestManifestHash[siteIdHash] = manifestHash;

        manifestsBySiteDate[siteIdHash][businessDate].push(
            manifestHash
        );

        emit AnchorRecorded(
            siteIdHash,
            businessDate,
            sequence,
            manifestHash,
            dailyRoot,
            msg.sender,
            anchoredAt
        );
    }

    function getByManifestHash(
        bytes32 manifestHash
    ) external view returns (Anchor memory) {
        if (!manifestExists[manifestHash]) {
            revert InvalidInput();
        }

        return anchorsByManifestHash[manifestHash];
    }

    function getBySiteDate(
        bytes32 siteIdHash,
        uint32 businessDate
    ) external view returns (Anchor[] memory) {
        bytes32[] storage hashes =
            manifestsBySiteDate[siteIdHash][businessDate];

        Anchor[] memory results =
            new Anchor[](hashes.length);

        for (uint256 i = 0; i < hashes.length; i++) {
            results[i] = anchorsByManifestHash[hashes[i]];
        }

        return results;
    }
}