# Appendix C verifier

這個目錄目前提供一個無第三方套件依賴的 Python 驗證器。測試資料放在本機 `frontend/fixtures/`，不包含在公開儲存庫中，執行前需自行備妥 `appendix_c.json`。

執行：

```bash
python3 frontend/verify_appendix_c.py
```

驗證內容：

- 10 筆附錄 A Record 的 `leafHash`
- 7 個固定類別的 `categoryRoot`
- `dailyRoot`
- `manifestHash`
- 測試憑證指紋與 ES256 簽章
- 附錄 C 的 EVENT 與 OPERATION inclusion proof

程式成功時 exit code 為 `0`，任何一項不符時為 `1`。

`jcs()` 針對本規格小數皆為字串、鍵名為 ASCII的資料限制實作，足以重現附錄 C。正式接收任意 JSON 時，應換成完整 RFC 8785 函式庫。

## Vue 驗證前端

`frontend/web` 是 Vue 3、Vite、TypeScript 前端。目前可在瀏覽器本機完成：

- 載入或拖放包含完整 `records[]` 的整日 JSON
- 重算全部 Record leafHash、Category Root 與 Daily Root
- 比對 Manifest 的分類筆數、分類根、dailyRoot 與 manifestHash
- 使用附錄 C 公鑰資料驗證 EMS ES256 簽章
- 直接以唯讀 RPC 查詢 Sepolia 智慧合約 Anchor

啟動：

```bash
cd frontend/web
npm install
npm run dev
```

正式建置：

```bash
npm run build
```

前端不會上傳或保存使用者載入的 Records，只有 `manifestHash` 會用於唯讀鏈上查詢。
