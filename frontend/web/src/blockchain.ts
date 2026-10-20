import { Contract, JsonRpcProvider, ZeroHash, getAddress, isError } from 'ethers'

import contractArtifact from './contracts/EnergyLedgerAnchor.json'
import type { JsonObject, JsonValue, VerificationCheck } from './types'

interface ContractAnchor {
  siteIdHash: string
  businessDate: bigint
  sequence: bigint
  dailyRoot: string
  manifestHash: string
  prevManifestHash: string
  amendsManifestHash: string
  submitter: string
  anchoredAt: bigint
}

export interface OnChainAnchor {
  siteIdHash: string
  businessDate: number
  sequence: number
  dailyRoot: string
  manifestHash: string
  prevManifestHash: string
  amendsManifestHash: string
  submitter: string
  transactionHash: string | null
  anchoredAt: number
  anchoredAtText: string
  source: 'rpc' | 'cache'
}

export interface ChainVerificationResult {
  check: VerificationCheck
  anchor?: OnChainAnchor
}

export interface PublicChainConfig {
  chainId: number
  networkName: string
  contractAddress: string
  deploymentBlock: number
  blockExplorerUrl: string
}

interface CachedAnchor {
  version: 2
  anchor: Omit<OnChainAnchor, 'source'>
}

let provider: JsonRpcProvider | undefined
let contract: Contract | undefined

function requiredEnvironment(name: keyof ImportMetaEnv): string {
  const value = import.meta.env[name]?.trim()
  if (!value) throw new Error(`前端缺少 ${name} 設定`)
  return value
}

export function getPublicChainConfig(): PublicChainConfig {
  const chainId = Number(requiredEnvironment('VITE_CHAIN_ID'))
  const deploymentBlock = Number(requiredEnvironment('VITE_DEPLOYMENT_BLOCK'))
  if (!Number.isSafeInteger(chainId) || chainId <= 0) {
    throw new Error('VITE_CHAIN_ID 必須是有效的正整數')
  }
  if (!Number.isSafeInteger(deploymentBlock) || deploymentBlock < 0) {
    throw new Error('VITE_DEPLOYMENT_BLOCK 必須是有效的區塊高度')
  }
  return {
    chainId,
    networkName: import.meta.env.VITE_CHAIN_NAME?.trim() || 'Ethereum Sepolia',
    contractAddress: getAddress(requiredEnvironment('VITE_CONTRACT_ADDRESS')),
    deploymentBlock,
    blockExplorerUrl: requiredEnvironment('VITE_BLOCK_EXPLORER_URL').replace(/\/$/, ''),
  }
}

function getClient(): { provider: JsonRpcProvider; contract: Contract } {
  if (provider && contract) return { provider, contract }
  const config = getPublicChainConfig()
  provider = new JsonRpcProvider(requiredEnvironment('VITE_RPC_URL'))
  contract = new Contract(config.contractAddress, contractArtifact.abi, provider)
  return { provider, contract }
}

async function blockNearTimestamp(
  rpcProvider: JsonRpcProvider,
  targetTimestamp: number,
  deploymentBlock: number,
): Promise<number> {
  let low = deploymentBlock
  let high = await rpcProvider.getBlockNumber()

  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    const block = await rpcProvider.getBlock(middle)
    if (!block) throw new Error(`找不到區塊 ${middle}`)
    if (block.timestamp < targetTimestamp) low = middle + 1
    else high = middle
  }
  return low
}

async function anchorTransactionHash(
  rpcProvider: JsonRpcProvider,
  anchorContract: Contract,
  config: PublicChainConfig,
  manifestHash: string,
  anchoredAt: number,
): Promise<string | null> {
  const candidate = await blockNearTimestamp(
    rpcProvider,
    anchoredAt,
    config.deploymentBlock,
  )
  const latestBlock = await rpcProvider.getBlockNumber()
  const fromBlock = Math.max(config.deploymentBlock, candidate - 2)
  const toBlock = Math.min(latestBlock, candidate + 2)
  const filter = anchorContract.filters.AnchorRecorded(
    null,
    null,
    null,
    manifestHash,
  )
  const events = await anchorContract.queryFilter(filter, fromBlock, toBlock)
  return events.at(-1)?.transactionHash ?? null
}

function normalizeBytes32(value: string): string {
  const normalized = value
    .replace(/^sha256:/i, '')
    .replace(/^0x/i, '')
    .toLowerCase()
  if (!/^[0-9a-f]{64}$/.test(normalized)) {
    throw new Error(`不是有效的 bytes32：${value}`)
  }
  return `0x${normalized}`
}

function manifestBusinessDate(value: JsonValue | undefined): number {
  if (typeof value !== 'string') throw new Error('Manifest 缺少 businessDate')
  const normalized = value.replaceAll('-', '')
  if (!/^\d{8}$/.test(normalized)) throw new Error('businessDate 格式錯誤')
  return Number(normalized)
}

function manifestSequence(value: JsonValue | undefined): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1) {
    throw new Error('Manifest sequence 格式錯誤')
  }
  return value
}

function amendmentHash(value: JsonValue | undefined): string {
  if (typeof value === 'string') return normalizeBytes32(value)
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const hash = value.manifestHash
    if (typeof hash === 'string') return normalizeBytes32(hash)
  }
  return ZeroHash
}

function nullableHash(value: JsonValue | undefined): string {
  return typeof value === 'string' ? normalizeBytes32(value) : ZeroHash
}

function cacheKey(config: PublicChainConfig, manifestHash: string): string {
  return [
    'energy-ledger-anchor-v2',
    config.chainId,
    config.contractAddress.toLowerCase(),
    manifestHash.toLowerCase(),
  ].join(':')
}

function readCachedAnchor(key: string): OnChainAnchor | undefined {
  try {
    const value = localStorage.getItem(key)
    if (!value) return undefined
    const cached = JSON.parse(value) as CachedAnchor
    if (cached.version !== 2) return undefined
    return { ...cached.anchor, source: 'cache' }
  } catch {
    return undefined
  }
}

function writeCachedAnchor(key: string, anchor: OnChainAnchor): void {
  const { source: _source, ...storedAnchor } = anchor
  const cached: CachedAnchor = { version: 2, anchor: storedAnchor }
  localStorage.setItem(key, JSON.stringify(cached))
}

function mapContractAnchor(
  value: ContractAnchor,
  source: 'rpc' | 'cache',
  transactionHash: string | null,
): OnChainAnchor {
  const anchoredAt = Number(value.anchoredAt)
  return {
    siteIdHash: value.siteIdHash.toLowerCase(),
    businessDate: Number(value.businessDate),
    sequence: Number(value.sequence),
    dailyRoot: value.dailyRoot.toLowerCase(),
    manifestHash: value.manifestHash.toLowerCase(),
    prevManifestHash: value.prevManifestHash.toLowerCase(),
    amendsManifestHash: value.amendsManifestHash.toLowerCase(),
    submitter: value.submitter,
    transactionHash,
    anchoredAt,
    anchoredAtText: new Intl.DateTimeFormat('zh-TW', {
      dateStyle: 'medium',
      timeStyle: 'medium',
      timeZone: 'Asia/Taipei',
    }).format(new Date(anchoredAt * 1000)),
    source,
  }
}

function compareAnchor(
  manifest: JsonObject,
  calculatedManifestHash: string,
  anchor: OnChainAnchor,
): string[] {
  const expected: Record<string, string | number> = {
    siteIdHash: normalizeBytes32(String(manifest.siteIdHash ?? '')),
    businessDate: manifestBusinessDate(manifest.businessDate),
    sequence: manifestSequence(manifest.sequence),
    dailyRoot: normalizeBytes32(String(manifest.dailyRoot ?? '')),
    manifestHash: normalizeBytes32(calculatedManifestHash),
    prevManifestHash: nullableHash(manifest.prevManifestHash),
    amendsManifestHash: amendmentHash(manifest.amends),
  }
  const actual: Record<string, string | number> = {
    siteIdHash: anchor.siteIdHash,
    businessDate: anchor.businessDate,
    sequence: anchor.sequence,
    dailyRoot: anchor.dailyRoot,
    manifestHash: anchor.manifestHash,
    prevManifestHash: anchor.prevManifestHash,
    amendsManifestHash: anchor.amendsManifestHash,
  }
  return Object.keys(expected).filter((key) => expected[key] !== actual[key])
}

function pendingCheck(detail: string): ChainVerificationResult {
  return {
    check: {
      id: 'blockchain',
      step: '07',
      label: '鏈上 Anchor',
      status: 'pending',
      detail,
    },
  }
}

export async function verifyManifestOnChain(
  manifest: JsonObject,
  calculatedManifestHash: string,
  forceRefresh = false,
): Promise<ChainVerificationResult> {
  let config: PublicChainConfig
  let normalizedManifestHash: string
  try {
    config = getPublicChainConfig()
    normalizedManifestHash = normalizeBytes32(calculatedManifestHash)
  } catch (error) {
    return pendingCheck(error instanceof Error ? error.message : '鏈上設定不完整')
  }

  const key = cacheKey(config, normalizedManifestHash)
  if (forceRefresh) localStorage.removeItem(key)
  let anchor = readCachedAnchor(key)

  try {
    if (!anchor) {
      const client = getClient()
      const rpcNetwork = await client.provider.getNetwork()
      if (rpcNetwork.chainId !== BigInt(config.chainId)) {
        return pendingCheck(
          `RPC chainId ${rpcNetwork.chainId} 與設定的 ${config.chainId} 不一致。`,
        )
      }
      const result = await client.contract.getByManifestHash(
        normalizedManifestHash,
      ) as unknown as ContractAnchor
      let transactionHash: string | null = null
      try {
        transactionHash = await anchorTransactionHash(
          client.provider,
          client.contract,
          config,
          normalizedManifestHash,
          Number(result.anchoredAt),
        )
      } catch {
        // Event 查詢失敗不影響 Anchor 內容驗證，只是不顯示交易連結。
      }
      anchor = mapContractAnchor(result, 'rpc', transactionHash)
      writeCachedAnchor(key, anchor)
    }

    const mismatches = compareAnchor(manifest, calculatedManifestHash, anchor)
    if (mismatches.length > 0) {
      return {
        anchor,
        check: {
          id: 'blockchain',
          step: '07',
          label: '鏈上 Anchor',
          status: 'fail',
          detail: `鏈上資料不一致：${mismatches.join('、')}。`,
        },
      }
    }

    return {
      anchor,
      check: {
        id: 'blockchain',
        step: '07',
        label: '鏈上 Anchor',
        status: 'pass',
        detail: anchor.source === 'cache'
          ? '快取的鏈上 Anchor 與本機重算結果一致。'
          : 'Sepolia Anchor 與本機重算的 Manifest、Daily Root 及鏈結資料一致。',
      },
    }
  } catch (error) {
    if (isError(error, 'CALL_EXCEPTION')) {
      return {
        check: {
          id: 'blockchain',
          step: '07',
          label: '鏈上 Anchor',
          status: 'fail',
          detail: '合約中找不到這個 manifestHash。',
        },
      }
    }
    const message = error instanceof Error ? error.message.toLowerCase() : ''
    if (message.includes('403') || message.includes('origin') || message.includes('whitelist')) {
      return pendingCheck('RPC 拒絕目前的網站來源，請將此網域加入 Alchemy Origin Allowlist。')
    }
    if (message.includes('429') || message.includes('rate limit') || message.includes('too many')) {
      return pendingCheck('RPC 查詢次數已達限制，請稍後再試。')
    }
    return pendingCheck('目前無法連接 Sepolia RPC，不能判定鏈上資料是否存在。')
  }
}
