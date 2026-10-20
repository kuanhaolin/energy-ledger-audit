<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  getPublicChainConfig,
  verifyManifestOnChain,
  type ChainVerificationResult,
} from './blockchain'
import type { CheckStatus, JsonObject, NormalizedDailyRecords, VerificationCheck } from './types'
import { normalizeDailyRecords, verifyBundle } from './verifier'

const idleChecks: VerificationCheck[] = [
  ['structure', '01', '整日 Records 格式'],
  ['leaf', '02', 'Record leafHash'],
  ['category', '03', 'Category Root'],
  ['daily', '04', 'Daily Root'],
  ['manifest-hash', '05', 'Manifest Hash'],
  ['signature', '06', 'EMS ES256 簽章'],
  ['blockchain', '07', '鏈上 Anchor'],
].map(([id, step, label]) => ({ id, step, label, status: 'idle' as CheckStatus, detail: '等待載入整日 Records JSON。' }))

const fileInput = ref<HTMLInputElement | null>(null)
const checks = ref<VerificationCheck[]>(idleChecks)
const bundle = ref<NormalizedDailyRecords | null>(null)
const sourceName = ref('')
const rawJson = ref('')
const errorMessage = ref('')
const isDragging = ref(false)
const isVerifying = ref(false)
const isChainVerifying = ref(false)
const activeCheckId = ref('structure')
const calculatedManifestHash = ref('')
const chainResult = ref<ChainVerificationResult | null>(null)

const chainConfig = (() => {
  try {
    return getPublicChainConfig()
  } catch {
    return null
  }
})()

const passCount = computed(() => checks.value.filter((item) => item.status === 'pass').length)
const failCount = computed(() => checks.value.filter((item) => item.status === 'fail').length)
const pendingCount = computed(() => checks.value.filter((item) => item.status === 'pending').length)
const completedCount = computed(() => passCount.value + failCount.value)
const completionProgress = computed(() => Math.round((completedCount.value / checks.value.length) * 100))
const passRate = computed(() => Math.round((passCount.value / checks.value.length) * 100))
const activeCheck = computed(() => checks.value.find((item) => item.id === activeCheckId.value) ?? checks.value[0])
const chainCheck = computed(() => checks.value.find((item) => item.id === 'blockchain') ?? idleChecks.at(-1)!)
const chainAnchor = computed(() => chainResult.value?.anchor)
const canQueryChain = computed(() => Boolean(
  bundle.value
  && calculatedManifestHash.value
  && !checks.value.some((item) => item.id !== 'blockchain' && item.status === 'fail'),
))
const networkStateLabel = computed(() => {
  if (!chainConfig) return '鏈上設定不完整'
  if (isChainVerifying.value) return '正在查詢 Sepolia'
  if (!bundle.value) return `${chainConfig.networkName} 待驗證`
  if (chainCheck.value.status === 'pass') return '鏈上存證驗證成功'
  if (chainCheck.value.status === 'fail') return '鏈上存證不一致'
  return '鏈上查詢待確認'
})
const networkStateClass = computed(() => {
  if (chainCheck.value.status === 'pass') return 'pass'
  if (chainCheck.value.status === 'fail') return 'fail'
  return 'pending'
})
const contractExplorerUrl = computed(() => chainConfig
  ? `${chainConfig.blockExplorerUrl}/address/${chainConfig.contractAddress}`
  : '')
const submitterExplorerUrl = computed(() => chainConfig && chainAnchor.value
  ? `${chainConfig.blockExplorerUrl}/address/${chainAnchor.value.submitter}`
  : '')
const transactionExplorerUrl = computed(() => chainConfig && chainAnchor.value?.transactionHash
  ? `${chainConfig.blockExplorerUrl}/tx/${chainAnchor.value.transactionHash}`
  : '')
const manifest = computed(() => bundle.value?.manifest)
const siteId = computed(() => stringValue(manifest.value?.siteId) || '—')
const businessDate = computed(() => formatBusinessDate(manifest.value?.businessDate))
const manifestHash = computed(() => stringValue(manifest.value?.manifestHash) || '—')
const dailyRoot = computed(() => stringValue(manifest.value?.dailyRoot) || '—')
const recordCount = computed(() => bundle.value?.records.length ?? 0)
const categoryCount = computed(() => new Set(
  (bundle.value?.records ?? []).map((item) => stringValue(item.category)).filter(Boolean),
).size)
const tradeRows = computed(() => (bundle.value?.records ?? [])
  .filter((item) => item.category === 'TRADE')
  .map((item) => {
    const payload = objectValue(item.payload)
    const sharing = objectValue(payload?.sharing)
    return {
      recordId: stringValue(item.recordId) || '—',
      tradeType: stringValue(payload?.tradeType) || 'TRADE',
      energyChargedKwh: decimalValue(payload?.energyChargedKwh),
      energyDischargedKwh: decimalValue(payload?.energyDischargedKwh),
      chargeCostTwd: decimalValue(payload?.chargeCostTwd),
      dischargeSavingTwd: decimalValue(payload?.dischargeSavingTwd),
      netBenefitTwd: decimalValue(payload?.netBenefitTwd),
      escoAmountTwd: decimalValue(sharing?.escoAmountTwd),
      ownerAmountTwd: decimalValue(sharing?.ownerAmountTwd),
    }
  }))
const transactionCount = computed(() => tradeRows.value.length.toLocaleString())
const earnings = computed(() => ({
  chargedKwh: sumValues(tradeRows.value.map((item) => item.energyChargedKwh)),
  dischargedKwh: sumValues(tradeRows.value.map((item) => item.energyDischargedKwh)),
  chargeCostTwd: sumValues(tradeRows.value.map((item) => item.chargeCostTwd)),
  dischargeSavingTwd: sumValues(tradeRows.value.map((item) => item.dischargeSavingTwd)),
  netBenefitTwd: sumValues(tradeRows.value.map((item) => item.netBenefitTwd)),
  escoAmountTwd: sumValues(tradeRows.value.map((item) => item.escoAmountTwd)),
  ownerAmountTwd: sumValues(tradeRows.value.map((item) => item.ownerAmountTwd)),
}))
const netBenefit = computed(() => formatTwd(earnings.value.netBenefitTwd))
const earningsTrust = computed(() => {
  if (failCount.value > 0) return { className: 'fail', label: '資料驗證失敗，收益不可採信' }
  if (chainCheck.value.status === 'pass') return { className: 'pass', label: '收益資料已通過本機與鏈上驗證' }
  return { className: 'pending', label: '本機資料一致，收益等待鏈上確認' }
})

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function objectValue(value: unknown): JsonObject | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : undefined
}

function decimalValue(value: unknown): number | null {
  if (typeof value !== 'string' && typeof value !== 'number') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function sumValues(values: Array<number | null>): number | null {
  const available = values.filter((value): value is number => value !== null)
  return available.length ? available.reduce((total, value) => total + value, 0) : null
}

function formatBusinessDate(value: unknown): string {
  if (typeof value !== 'string' && typeof value !== 'number') return '—'
  const raw = String(value)
  return /^\d{8}$/.test(raw) ? `${raw.slice(0, 4)} / ${raw.slice(4, 6)} / ${raw.slice(6)}` : raw
}

function formatTwd(value: number | null): string {
  if (value === null) return '未提供'
  return new Intl.NumberFormat('zh-TW', {
    style: 'currency',
    currency: 'TWD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

function formatEnergy(value: number | null): string {
  if (value === null) return '未提供'
  return `${new Intl.NumberFormat('zh-TW', {
    minimumFractionDigits: 3,
    maximumFractionDigits: 3,
  }).format(value)} kWh`
}

function shortHash(value?: string): string {
  if (!value || value === '—') return '—'
  const normalized = value.replace(/^sha256:/, '')
  return normalized.length > 22 ? `${normalized.slice(0, 12)}…${normalized.slice(-8)}` : normalized
}

function statusLabel(status: CheckStatus): string {
  return { idle: '等待中', pass: '通過', fail: '不通過', pending: '待確認' }[status]
}

function replaceCheck(check: VerificationCheck) {
  checks.value = checks.value.map((item) => item.id === check.id ? check : item)
}

async function runChainVerification(forceRefresh = false) {
  if (!bundle.value || !calculatedManifestHash.value) return
  if (!canQueryChain.value) {
    replaceCheck({
      id: 'blockchain',
      step: '07',
      label: '鏈上 Anchor',
      status: 'pending',
      detail: '本機驗證未通過，因此沒有送出鏈上查詢。',
    })
    return
  }

  isChainVerifying.value = true
  activeCheckId.value = 'blockchain'
  replaceCheck({
    id: 'blockchain',
    step: '07',
    label: '鏈上 Anchor',
    status: 'pending',
    detail: '正在以唯讀 RPC 查詢 Sepolia 合約。',
  })
  try {
    chainResult.value = await verifyManifestOnChain(
      bundle.value.manifest,
      calculatedManifestHash.value,
      forceRefresh,
    )
    replaceCheck(chainResult.value.check)
  } finally {
    isChainVerifying.value = false
  }
}

async function processBundle(raw: unknown, name: string) {
  isVerifying.value = true
  errorMessage.value = ''
  sourceName.value = name
  activeCheckId.value = 'structure'
  chainResult.value = null
  calculatedManifestHash.value = ''
  try {
    bundle.value = normalizeDailyRecords(raw)
    rawJson.value = JSON.stringify(raw, null, 2)
    const result = await verifyBundle(raw)
    bundle.value = result.bundle
    checks.value = result.checks
    calculatedManifestHash.value = result.manifestHash ?? ''
    activeCheckId.value = result.checks.find((item) => item.status === 'fail')?.id
      ?? result.checks.find((item) => item.status === 'pass')?.id
      ?? 'structure'
    await runChainVerification()
  } catch (error) {
    bundle.value = null
    chainResult.value = null
    calculatedManifestHash.value = ''
    rawJson.value = ''
    checks.value = idleChecks
    errorMessage.value = error instanceof Error ? error.message : '無法讀取這份檔案。'
  } finally {
    isVerifying.value = false
  }
}

async function handleFile(file?: File) {
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.json')) {
    errorMessage.value = '目前只接受 .json 格式的整日 Records 檔案。'
    return
  }
  try {
    await processBundle(JSON.parse(await file.text()) as unknown, file.name)
  } catch {
    errorMessage.value = 'JSON 解析失敗，請確認檔案格式。'
  }
}

function onDrop(event: DragEvent) {
  isDragging.value = false
  void handleFile(event.dataTransfer?.files[0])
}

function reset() {
  bundle.value = null
  checks.value = idleChecks
  sourceName.value = ''
  rawJson.value = ''
  errorMessage.value = ''
  activeCheckId.value = 'structure'
  chainResult.value = null
  calculatedManifestHash.value = ''
  if (fileInput.value) fileInput.value.value = ''
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#" aria-label="能源存證驗證台首頁">
        <span class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 32 32"><path d="M16 3 27 9.5v13L16 29 5 22.5v-13L16 3Z"/><path d="m10.5 17 4 4 7.5-10"/></svg>
        </span>
        <span><strong>能源存證驗證台</strong><small>ENERGY PROOF VERIFIER</small></span>
      </a>
      <div class="network-state" :class="networkStateClass"><span class="state-dot"></span>{{ networkStateLabel }}</div>
    </header>

    <main>
      <section class="hero">
        <div class="hero-copy">
          <p class="eyebrow"><span>ENERGY DATA AUDIT</span>能源資料驗證</p>
          <h1>驗證能源紀錄，<br><em>確認鏈上存證。</em></h1>
          <p class="hero-description">匯入 EMS 每日紀錄，自動檢查資料完整性、比對鏈上存證，並顯示當日收益。</p>
          <div class="hero-points" aria-label="驗證特點">
            <span><i>01</i> 資料驗證</span><span><i>02</i> 鏈上存證</span><span><i>03</i> 收益統計</span>
          </div>
        </div>

        <div
          class="upload-card" :class="{ dragging: isDragging, loaded: bundle }"
          @dragenter.prevent="isDragging = true" @dragover.prevent="isDragging = true"
          @dragleave.prevent="isDragging = false" @drop.prevent="onDrop"
        >
          <template v-if="!bundle">
            <div class="upload-icon" aria-hidden="true">
              <svg viewBox="0 0 40 40"><path d="M20 27V9m0 0-7 7m7-7 7 7"/><path d="M8 25v5a3 3 0 0 0 3 3h18a3 3 0 0 0 3-3v-5"/></svg>
            </div>
            <p class="upload-kicker">DAILY RECORDS</p>
            <h2>拖放 JSON 到這裡</h2>
            <p>或從電腦選擇 EMS 匯出的驗證資料</p>
            <button class="primary-button" type="button" @click="fileInput?.click()">選擇 JSON 檔案</button>
          </template>
          <template v-else>
            <div class="loaded-file">
              <span class="file-icon">JSON</span>
              <div><p>已載入驗證資料</p><strong>{{ sourceName }}</strong></div>
              <button class="icon-button" type="button" aria-label="移除檔案" @click="reset">×</button>
            </div>
            <div
              class="result-orbit"
              :class="{ danger: failCount > 0 }"
              :style="{ '--progress': `${passRate}%` }"
            >
              <div><strong>{{ passRate }}<small>%</small></strong><span>驗證通過率</span></div>
            </div>
            <div class="quick-result">
              <span class="pass"><b>{{ passCount }}</b> 通過</span>
              <span v-if="failCount" class="fail"><b>{{ failCount }}</b> 失敗</span>
              <span class="pending"><b>{{ pendingCount }}</b> 待確認</span>
            </div>
          </template>
          <input ref="fileInput" class="visually-hidden" type="file" accept="application/json,.json" @change="handleFile(($event.target as HTMLInputElement).files?.[0])">
        </div>
      </section>

      <div v-if="errorMessage" class="error-banner" role="alert">
        <strong>無法驗證</strong><span>{{ errorMessage }}</span><button type="button" @click="errorMessage = ''">關閉</button>
      </div>

      <section v-if="bundle" class="workspace" aria-live="polite">
        <div class="summary-bar">
          <div class="summary-heading">
            <span class="verified-seal" :class="{ danger: failCount > 0 }">
              <svg v-if="failCount === 0" viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"/></svg>
              <svg v-else viewBox="0 0 24 24"><path d="m7 7 10 10M17 7 7 17"/></svg>
            </span>
            <div><p>{{ failCount ? '發現不一致項目' : '驗證已完成' }}</p><strong>{{ sourceName }}</strong></div>
          </div>
          <dl>
            <div><dt>案場</dt><dd>{{ siteId }}</dd></div>
            <div><dt>營業日</dt><dd>{{ businessDate }}</dd></div>
            <div><dt>交易筆數</dt><dd>{{ transactionCount }}</dd></div>
            <div><dt>今日淨收益</dt><dd>{{ netBenefit }}</dd></div>
          </dl>
        </div>

        <section class="panel earnings-panel">
          <div class="earnings-heading">
            <div>
              <p class="eyebrow">DAILY PERFORMANCE</p>
              <h2>今日做了多少、賺了多少</h2>
              <p>{{ tradeRows.length }} 筆 TRADE 結算 · {{ businessDate }}</p>
            </div>
            <span class="earnings-trust" :class="earningsTrust.className">
              <i></i>{{ earningsTrust.label }}
            </span>
          </div>

          <div v-if="tradeRows.length" class="earnings-overview">
            <div class="net-benefit-card">
              <span>今日淨收益</span>
              <strong>{{ formatTwd(earnings.netBenefitTwd) }}</strong>
            </div>
            <dl class="performance-metrics">
              <div><dt>充電量</dt><dd>{{ formatEnergy(earnings.chargedKwh) }}</dd></div>
              <div><dt>放電量</dt><dd>{{ formatEnergy(earnings.dischargedKwh) }}</dd></div>
              <div><dt>放電節省</dt><dd>{{ formatTwd(earnings.dischargeSavingTwd) }}</dd></div>
              <div><dt>充電成本</dt><dd>{{ formatTwd(earnings.chargeCostTwd) }}</dd></div>
            </dl>
          </div>

          <div v-if="tradeRows.length" class="trade-table-wrap">
            <table class="trade-table">
              <thead>
                <tr>
                  <th>交易類型</th>
                  <th>充電量</th>
                  <th>放電量</th>
                  <th>充電成本</th>
                  <th>放電節省</th>
                  <th>淨收益</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in tradeRows" :key="item.recordId">
                  <td><strong>{{ item.tradeType }}</strong><small :title="item.recordId">{{ shortHash(item.recordId) }}</small></td>
                  <td>{{ formatEnergy(item.energyChargedKwh) }}</td>
                  <td>{{ formatEnergy(item.energyDischargedKwh) }}</td>
                  <td>{{ formatTwd(item.chargeCostTwd) }}</td>
                  <td>{{ formatTwd(item.dischargeSavingTwd) }}</td>
                  <td class="income">{{ formatTwd(item.netBenefitTwd) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="tradeRows.length && (earnings.escoAmountTwd !== null || earnings.ownerAmountTwd !== null)" class="sharing-summary">
            <span>收益分配</span>
            <div><small>ESCO</small><strong>{{ formatTwd(earnings.escoAmountTwd) }}</strong></div>
            <div><small>業主</small><strong>{{ formatTwd(earnings.ownerAmountTwd) }}</strong></div>
          </div>

          <p v-if="!tradeRows.length" class="no-trades">這份 Records 沒有 TRADE 資料，因此無法顯示收益。</p>
        </section>

        <div class="verification-layout">
          <section class="panel checks-panel">
            <div class="panel-heading">
              <div><p class="eyebrow">VERIFICATION PATH</p><h2>驗證流程</h2></div>
              <span>{{ completedCount }} / {{ checks.length }} 已檢查</span>
            </div>
            <div class="progress-track"><i :style="{ width: `${completionProgress}%` }"></i></div>
            <div class="check-list">
              <button
                v-for="item in checks" :key="item.id" type="button" class="check-row"
                :class="[item.status, { active: activeCheckId === item.id }]" @click="activeCheckId = item.id"
              >
                <span class="check-step">{{ item.step }}</span>
                <span class="check-copy"><strong>{{ item.label }}</strong><small>{{ item.detail }}</small></span>
                <span class="status-badge"><i></i>{{ statusLabel(item.status) }}</span>
              </button>
            </div>
          </section>

          <aside class="details-column">
            <section class="panel detail-panel">
              <div class="detail-topline">
                <span>STEP {{ activeCheck.step }}</span>
                <span class="status-badge" :class="activeCheck.status"><i></i>{{ statusLabel(activeCheck.status) }}</span>
              </div>
              <h2>{{ activeCheck.label }}</h2><p>{{ activeCheck.detail }}</p>
              <div v-if="activeCheck.expected || activeCheck.actual" class="hash-compare">
                <div v-if="activeCheck.expected"><span>檔案中的值</span><code>{{ activeCheck.expected }}</code></div>
                <div v-if="activeCheck.actual"><span>瀏覽器重算值</span><code>{{ activeCheck.actual }}</code></div>
              </div>
              <div class="dataset-meta">
                <div><span>Records 數量</span><strong>{{ recordCount }}</strong></div>
                <div><span>Category 數量</span><strong>{{ categoryCount }}</strong></div>
                <div><span>Daily Root</span><code :title="dailyRoot">{{ shortHash(dailyRoot) }}</code></div>
                <div><span>Manifest Hash</span><code :title="manifestHash">{{ shortHash(manifestHash) }}</code></div>
              </div>
            </section>

            <section class="panel chain-panel">
              <div class="chain-title">
                <span class="chain-symbol" aria-hidden="true">⌁</span>
                <div><p class="eyebrow">ON-CHAIN ANCHOR</p><h2>鏈上驗證</h2></div>
                <span class="status-badge" :class="chainCheck.status"><i></i>{{ statusLabel(chainCheck.status) }}</span>
              </div>
              <p>{{ chainCheck.detail }}</p>
              <dl class="chain-fields">
                <div><dt>Network</dt><dd>{{ chainConfig?.networkName ?? '設定不完整' }}</dd></div>
                <div>
                  <dt>Contract</dt>
                  <dd><a v-if="contractExplorerUrl" :href="contractExplorerUrl" target="_blank" rel="noreferrer">{{ shortHash(chainConfig?.contractAddress) }}</a><span v-else>—</span></dd>
                </div>
                <div><dt>Chain ID</dt><dd>{{ chainConfig?.chainId ?? '—' }}</dd></div>
                <div><dt>Sequence</dt><dd>{{ chainAnchor?.sequence ?? '—' }}</dd></div>
                <div><dt>Anchored At</dt><dd>{{ chainAnchor?.anchoredAtText ?? '—' }}</dd></div>
                <div>
                  <dt>Submitter</dt>
                  <dd><a v-if="submitterExplorerUrl" :href="submitterExplorerUrl" target="_blank" rel="noreferrer">{{ shortHash(chainAnchor?.submitter) }}</a><span v-else>—</span></dd>
                </div>
                <div>
                  <dt>Transaction</dt>
                  <dd><a v-if="transactionExplorerUrl" :href="transactionExplorerUrl" target="_blank" rel="noreferrer">{{ shortHash(chainAnchor?.transactionHash ?? undefined) }}</a><span v-else>—</span></dd>
                </div>
                <div><dt>Source</dt><dd>{{ chainAnchor?.source === 'cache' ? '瀏覽器快取' : chainAnchor ? 'Sepolia RPC' : '—' }}</dd></div>
              </dl>
              <button type="button" :disabled="!canQueryChain || isChainVerifying" @click="runChainVerification(true)">
                {{ isChainVerifying ? '正在查詢…' : '重新查詢鏈上資料' }}
              </button>
            </section>
          </aside>
        </div>

        <details class="json-panel">
          <summary><span><b>{ }</b> 查看原始整日 Records JSON</span><small>僅顯示於本機</small></summary>
          <pre>{{ rawJson }}</pre>
        </details>
      </section>

      <section v-else class="empty-preview">
        <div class="preview-line"></div>
        <div><span>載入後會依序檢查</span><p>JSON 結構 → 全部 Record Hash → Category Root → Daily Root → Manifest → EMS 簽章 → Sepolia Anchor</p></div>
      </section>
    </main>

    <footer><span>Verifier · v0.3</span></footer>
    <div v-if="isVerifying" class="loading-overlay" role="status"><span></span>{{ isChainVerifying ? '正在查詢 Sepolia 鏈上存證…' : '正在重算雜湊與驗證路徑…' }}</div>
  </div>
</template>
