# Energy Ledger Notary Backend

FastAPI 實作中央端驗證與上鏈流程。

## 檔案結構

```text
backend/
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
├── app/
│   ├── api/
│   ├── blockchain/
│   ├── cli/
│   ├── contracts/
│   ├── domain/
│   ├── schemas/
│   ├── services/
│   ├── config.py
│   ├── main.py
│   ├── repository.py
│   └── worker.py
└── tests/
    ├── test_blockchain_event.py
    ├── test_error_redaction.py
    └── test_manifest_pipeline.py
```

本機設定 `.env`、金鑰、資料庫與測試資料不包含在公開儲存庫中。

## 資料狀態

程式內部為了 Worker 排程，使用以下狀態：

```text
validate-only：VALIDATED

live：QUEUED → SUBMITTING → FINALIZED
                              └→ FAILED
```

`VALIDATED` 代表第 1～5 步成功，但尚未上鏈。`FINALIZED` 代表交易已確認並產生已簽署 Receipt。

POST 建立新 Anchor 時回傳 `RECEIVED`，後續可用 GET 查詢驗證及上鏈狀態。

## 已實作流程

`POST /api/v1/anchors` 會依序執行：

1. 依 `schemaVersion` 選擇 JSON Schema。
2. 移除 `manifestHash`、`signatures`，以 RFC 8785 JCS + SHA-256 重算雜湊。
3. 查詢已登錄的 EMS X.509 憑證，檢查 `signedAt` 有效期並驗證至少一筆 PLATFORM ES256 簽章。
4. 以最後一筆 `FINALIZED` 紀錄檢查 `sequence` 及 `prevManifestHash`。
5. AMENDMENT 檢查目標必須已存在，且 `siteIdHash`、`businessDate` 相同。
6. live 模式將工作排隊，背景送至 `anchor()`，等待指定確認數後產生中央端 ES256 Receipt。

SQLite 只保存可信憑證、必要的 Anchor 狀態與 Receipt，不保存完整 Manifest、Record 或原始交易資料。

## 啟動

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload --port 8000
```

開發介面：

- Swagger：`http://localhost:8000/docs`
- Health：`http://localhost:8000/api/v1/health`
- 鏈設定：`http://localhost:8000/api/v1/chain/config`
- RPC 狀態：`http://localhost:8000/api/v1/chain/status`

## 先登錄 EMS 憑證

EMS 正式送資料前，中央端必須先把該案場憑證加入 trust store：

```bash
python -m app.cli.register_certificate \
  --pem /path/to/ems-certificate.pem \
  --signer-id PWR-EMS-SAAS-01 \
  --site-id-hash sha256:完整的64位hex
```

這是管理員操作，不提供公開註冊 API，避免 EMS 自己把任意憑證設成可信。若正式環境開啟 `MTLS_REQUIRED=true`，TLS 應由可信反向代理終止，代理必須先移除外部同名 Header，再寫入 `X-Client-Cert-Fingerprint` 與 `X-Client-Site-Id-Hash`。

如果中央端不是從 sequence 1 開始接資料，必須先核對鏈上資料，再把前一筆已錨定資料匯入本地 checkpoint；否則系統會正確回覆 `409 CHAIN_BROKEN`，不會自行猜測前值：

```bash
python -m app.cli.import_checkpoint \
  --manifest-id EXAMPLE-SITE:2026-09-02 \
  --site-id-hash sha256:完整的64位hex \
  --business-date 2026-09-02 \
  --sequence 246 \
  --daily-root 完整的64位hex \
  --manifest-hash 完整的64位hex \
  --prev-manifest-hash 完整的64位hex
```

此命令是受信任管理員的移轉工具；執行前須先在區塊鏈瀏覽器或舊 Receipt 確認內容。
