# Contract artifact

Anchor 合約部署並編譯後，將 ABI 或完整 artifact 放在：

```text
EnergyLedgerAnchor.json
```

後端同時接受：

```json
[{ "type": "function" }]
```

或含有 `abi` 欄位的完整 artifact：

```json
{
  "abi": [{ "type": "function" }],
  "bytecode": "0x..."
}
```

