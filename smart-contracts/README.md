# 智慧合約

使用 Hardhat 編譯、部署與驗證 `EnergyLedgerAnchor`。部署腳本會檢查部署錢包的權限，並把 ABI 同步到後端與前端。

## 資料夾結構

```text
smart-contracts/
├── contracts/          Solidity 合約
├── scripts/            部署與 Etherscan 驗證腳本
├── deployments/        本機部署紀錄與公開範例
├── hardhat.config.ts   網路與編譯設定
└── .env.example        環境變數欄位範例
```

## 安裝與設定

在專案根目錄執行：

```bash
cd smart-contracts
npm install
cp .env.example .env
npm run compile
```

部署至 Sepolia 前，在 `.env` 填入 `SEPOLIA_RPC_URL` 和 `SEPOLIA_PRIVATE_KEY`，並確認測試錢包有 Sepolia ETH。驗證原始碼時還需要 `ETHERSCAN_API_KEY`。

## 部署

部署本機：

```bash
cd smart-contracts
npm run node
```

```bash
cd smart-contracts
npm run deploy:local
```

部署至 Sepolia：

```bash
cd smart-contracts
npm run deploy:sepolia
```

部署成功後，腳本會自動產生 `deployments/localhost.json` 或 `deployments/sepolia.json`。這些檔案記錄合約地址、部署錢包、交易雜湊和區塊高度，留在本機且已被 Git 忽略。可提交的範例是欄位留空的 [`deployments/sepolia.example.json`](deployments/sepolia.example.json)。驗證腳本預設讀取實際的 `sepolia.json`，不會讀取 example。腳本也會更新 `backend/app/contracts/EnergyLedgerAnchor.json` 和 `frontend/web/src/contracts/EnergyLedgerAnchor.json` 的 ABI。

## 在 Etherscan 驗證原始碼

在 `.env` 填入 `ETHERSCAN_API_KEY` 後執行：

```bash
npm run verify:sepolia
```

腳本預設從 `deployments/sepolia.json` 讀取合約地址。若要驗證其他地址，可在 `.env` 設定 `VERIFY_CONTRACT_ADDRESS`。驗證成功後，原始碼會顯示在 Sepolia Etherscan 的 `Contract → Code` 頁籤。
