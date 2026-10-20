# 能源存證驗證台

Vue 3 + Vite + TypeScript 的公開整日 Records 驗證前端。原始資料只在瀏覽器內計算；鏈上步驟直接以唯讀 RPC 查詢 Sepolia 智慧合約，不需要連接錢包，也不會呼叫後端 API。

## 環境設定

複製範例並填入前端專用的唯讀 RPC URL：

```bash
cp .env.example .env
```

```dotenv
VITE_CHAIN_ID=11155111
VITE_CHAIN_NAME=Ethereum Sepolia
VITE_RPC_URL=https://eth-sepolia.g.alchemy.com/v2/<前端唯讀 App Key>
VITE_CONTRACT_ADDRESS=<部署後的合約地址>
VITE_DEPLOYMENT_BLOCK=<部署區塊高度>
VITE_BLOCK_EXPLORER_URL=https://sepolia.etherscan.io
```

所有 `VITE_` 變數都會進入瀏覽器成品，不能放私鑰或真正的秘密。Alchemy 建議使用獨立的前端 App Key，並限制允許的網域及流量。

## 執行

```bash
npm install
npm run dev
```

開發伺服器預設為 `http://localhost:5173`。

目前已完成：

- 整日 Records JSON 拖放（附錄 C 測試資料需由使用者自行備妥）
- 重算所有 Record leafHash、Category Root、Daily Root 與 Manifest Hash
- 比對 Manifest 的分類筆數與分類根
- EMS ES256 簽章驗證
- 加總並列出 TRADE 的充放電量、成本、節省、淨收益與收益分配
- 使用 `manifestHash` 呼叫合約 `getByManifestHash()`
- 比對鏈上的案場、營業日、序號、Daily Root、Manifest Hash、前筆與補正雜湊
- Etherscan 合約與提交者連結
- 成功查詢的瀏覽器快取，以及手動強制重新查詢

前端不負責送交易、不需要錢包，也不透過 Notary API 查詢驗證結果。

輸入檔案至少需要：

```json
{
  "records": [],
  "manifest": {},
  "certificate": {}
}
```

正式建置：

```bash
npm run build
```
