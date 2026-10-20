import type {
  CertificateEntry,
  DailyRecordsInput,
  JsonObject,
  JsonValue,
  NormalizedDailyRecords,
  VerificationCheck,
  VerificationResult,
} from './types'

const encoder = new TextEncoder()
const CATEGORY_ORDER = [
  'OPERATION',
  'EVENT',
  'TRADE',
  'STRATEGY',
  'AUDIT',
  'HEALTH',
  'METER',
] as const

function canonicalize(value: JsonValue): string {
  if (value === null) return 'null'
  if (typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value)
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('JCS 不接受 NaN 或 Infinity')
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(',')}]`

  const members = Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`)
  return `{${members.join(',')}}`
}

export function jcs(value: JsonValue): Uint8Array {
  return encoder.encode(canonicalize(value))
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const output = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0))
  let offset = 0
  for (const part of parts) {
    output.set(part, offset)
    offset += part.length
  }
  return output
}

function normalizeHash(value: string): string {
  return value.replace(/^sha256:/i, '').replace(/^0x/i, '').toLowerCase()
}

export function hexToBytes(value: string): Uint8Array {
  const hex = normalizeHash(value)
  if (!/^[0-9a-f]*$/.test(hex) || hex.length % 2 !== 0) {
    throw new Error(`不是有效的十六進位字串：${value}`)
  }
  const output = new Uint8Array(hex.length / 2)
  for (let index = 0; index < output.length; index += 1) {
    output[index] = Number.parseInt(hex.slice(index * 2, index * 2 + 2), 16)
  }
  return output
}

export function bytesToHex(value: Uint8Array): string {
  return Array.from(value, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function sha256(value: Uint8Array): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', value.slice().buffer))
}

async function leafHash(record: JsonObject): Promise<Uint8Array> {
  return sha256(concat(new Uint8Array([0x00]), jcs(record)))
}

async function nodeHash(left: Uint8Array, right: Uint8Array): Promise<Uint8Array> {
  return sha256(concat(new Uint8Array([0x01]), left, right))
}

async function merkleRoot(nodes: Uint8Array[]): Promise<Uint8Array> {
  if (nodes.length === 0) return new Uint8Array(32)

  let level = [...nodes]
  while (level.length > 1) {
    const nextLevel: Uint8Array[] = []
    for (let index = 0; index < level.length; index += 2) {
      const left = level[index]
      if (!left) throw new Error('Merkle Tree 節點不存在')
      const right = level[index + 1]
      nextLevel.push(right ? await nodeHash(left, right) : left)
    }
    level = nextLevel
  }
  const root = level[0]
  if (!root) throw new Error('無法建立 Merkle Root')
  return root
}

function compareUtf8(left: string, right: string): number {
  const leftBytes = encoder.encode(left)
  const rightBytes = encoder.encode(right)
  const length = Math.min(leftBytes.length, rightBytes.length)
  for (let index = 0; index < length; index += 1) {
    const difference = (leftBytes[index] ?? 0) - (rightBytes[index] ?? 0)
    if (difference !== 0) return difference
  }
  return leftBytes.length - rightBytes.length
}

function manifestBody(manifest: JsonObject): JsonObject {
  return Object.fromEntries(
    Object.entries(manifest).filter(([key]) => !['manifestHash', 'signatures'].includes(key)),
  ) as JsonObject
}

function decodeBase64Url(value: string): Uint8Array {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/')
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')
  return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0))
}

function encodeBase64Url(value: Uint8Array): string {
  let binary = ''
  value.forEach((byte) => { binary += String.fromCharCode(byte) })
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function certificateDer(pem: string): Uint8Array {
  const body = pem
    .replace(/-----BEGIN CERTIFICATE-----/g, '')
    .replace(/-----END CERTIFICATE-----/g, '')
    .replace(/\s/g, '')
  return Uint8Array.from(atob(body), (char) => char.charCodeAt(0))
}

async function verifyEs256(body: JsonObject, signature: string, certificate: CertificateEntry) {
  if (!certificate.publicKeyX || !certificate.publicKeyY) return null
  const publicKey = await crypto.subtle.importKey(
    'jwk',
    {
      kty: 'EC', crv: 'P-256',
      x: encodeBase64Url(hexToBytes(certificate.publicKeyX)),
      y: encodeBase64Url(hexToBytes(certificate.publicKeyY)),
      ext: true,
    },
    { name: 'ECDSA', namedCurve: 'P-256' },
    false,
    ['verify'],
  )
  return crypto.subtle.verify(
    { name: 'ECDSA', hash: 'SHA-256' },
    publicKey,
    decodeBase64Url(signature).slice().buffer,
    jcs(body).slice().buffer,
  )
}

function asObject(value: JsonValue | undefined, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} 必須是 JSON object`)
  }
  return value
}

export function normalizeDailyRecords(rawValue: unknown): NormalizedDailyRecords {
  if (!rawValue || typeof rawValue !== 'object' || Array.isArray(rawValue)) {
    throw new Error('最外層必須是 JSON object')
  }
  const raw = rawValue as DailyRecordsInput
  const manifest = asObject(raw.manifest, 'manifest')
  if (!Array.isArray(raw.records) || raw.records.length === 0) {
    throw new Error('找不到完整的 records[]')
  }
  const records = raw.records.map((item, index) => asObject(item, `records[${index}]`))
  const record = records[0]
  if (!record) throw new Error('records[] 不可為空')

  const firstSignature = Array.isArray(manifest.signatures) ? manifest.signatures[0] : undefined
  const fingerprint = firstSignature && typeof firstSignature === 'object' && !Array.isArray(firstSignature)
    ? (firstSignature.certificateFingerprint ?? firstSignature.certFingerprint)
    : undefined
  const certificate = raw.certificate
    ?? raw.certificates?.find((item) => !fingerprint || item.fingerprint === fingerprint)
    ?? raw.certificates?.[0]

  return { raw, records, record, manifest, certificate, receipt: raw.receipt }
}

function comparedCheck(
  id: string,
  step: string,
  label: string,
  actual: string,
  expected: string,
  successDetail: string,
  failDetail: string,
): VerificationCheck {
  const matches = normalizeHash(actual) === normalizeHash(expected)
  return {
    id, step, label, actual, expected,
    status: matches ? 'pass' : 'fail',
    detail: matches ? successDetail : failDetail,
  }
}

export async function verifyBundle(raw: unknown): Promise<VerificationResult> {
  const bundle = normalizeDailyRecords(raw)
  const { records, manifest, certificate } = bundle
  const checks: VerificationCheck[] = [{
    id: 'structure', step: '01', label: '整日 Records 格式', status: 'pass',
    detail: `已找到 ${records.length} 筆 Record 與 Daily Manifest。`,
  }]

  const manifestDailyRoot = typeof manifest.dailyRoot === 'string' ? manifest.dailyRoot : ''
  const recordsById = new Map<string, JsonObject>()
  const recordsByCategory = new Map<string, JsonObject[]>()
  const calculatedLeaves = new Map<string, Uint8Array>()
  const recordErrors: string[] = []

  for (const item of records) {
    const recordId = typeof item.recordId === 'string' ? item.recordId : ''
    const category = typeof item.category === 'string' ? item.category : ''
    if (!recordId || !category) {
      recordErrors.push('有 Record 缺少 recordId 或 category')
      continue
    }
    if (recordsById.has(recordId)) {
      recordErrors.push(`recordId 重複：${recordId}`)
      continue
    }
    recordsById.set(recordId, item)
    const categoryRecords = recordsByCategory.get(category) ?? []
    categoryRecords.push(item)
    recordsByCategory.set(category, categoryRecords)
    calculatedLeaves.set(recordId, await leafHash(item))
  }

  const expectedLeafHashes = bundle.raw.expected?.leafHashes
  if (expectedLeafHashes && typeof expectedLeafHashes === 'object' && !Array.isArray(expectedLeafHashes)) {
    for (const [recordId, calculated] of calculatedLeaves) {
      const expected = expectedLeafHashes[recordId]
      if (typeof expected === 'string' && normalizeHash(bytesToHex(calculated)) !== normalizeHash(expected)) {
        recordErrors.push(`${recordId} 的 leafHash 不一致`)
      }
    }
  }

  checks.push({
    id: 'leaf', step: '02', label: 'Record leafHash',
    status: recordErrors.length === 0 ? 'pass' : 'fail',
    detail: recordErrors.length === 0
      ? `已用 JCS＋SHA-256 重算 ${calculatedLeaves.size} 筆 Record leafHash。`
      : recordErrors.slice(0, 3).join('；'),
  })

  let dailyHex = ''
  {
    const manifestCategories = new Map<string, { count?: number; root?: string }>()
    if (Array.isArray(manifest.categories)) {
      for (const item of manifest.categories) {
        if (!item || typeof item !== 'object' || Array.isArray(item) || typeof item.category !== 'string') continue
        manifestCategories.set(item.category, {
          count: typeof item.count === 'number' ? item.count : undefined,
          root: typeof item.root === 'string' ? item.root : undefined,
        })
      }
    }

    const categoryRoots = new Map<string, Uint8Array>()
    const categoryErrors: string[] = []
    const unknownCategories = [...recordsByCategory.keys()].filter(
      (category) => !CATEGORY_ORDER.includes(category as typeof CATEGORY_ORDER[number]),
    )
    if (unknownCategories.length) categoryErrors.push(`未知分類：${unknownCategories.join('、')}`)

    for (const category of CATEGORY_ORDER) {
      const categoryRecords = [...(recordsByCategory.get(category) ?? [])]
        .sort((left, right) => compareUtf8(String(left.recordId), String(right.recordId)))
      const leaves = categoryRecords
        .map((item) => calculatedLeaves.get(String(item.recordId)))
        .filter((item): item is Uint8Array => Boolean(item))
      const root = await merkleRoot(leaves)
      categoryRoots.set(category, root)

      const expected = manifestCategories.get(category)
      if (!expected) {
        categoryErrors.push(`${category} 未出現在 Manifest categories`)
        continue
      }
      if (expected.count !== categoryRecords.length) {
        categoryErrors.push(`${category} 筆數應為 ${expected.count ?? '未提供'}，實際為 ${categoryRecords.length}`)
      }
      if (!expected.root || normalizeHash(bytesToHex(root)) !== normalizeHash(expected.root)) {
        categoryErrors.push(`${category} root 不一致`)
      }
    }

    checks.push({
      id: 'category', step: '03', label: 'Category Root',
      status: categoryErrors.length === 0 ? 'pass' : 'fail',
      detail: categoryErrors.length === 0
        ? `已依 recordId 排序並重算 ${CATEGORY_ORDER.length} 個 Category Root，全部與 Manifest 一致。`
        : categoryErrors.slice(0, 4).join('；'),
    })

    const calculatedDailyRoot = await merkleRoot(
      CATEGORY_ORDER.map((category) => categoryRoots.get(category) ?? new Uint8Array(32)),
    )
    dailyHex = bytesToHex(calculatedDailyRoot)
    checks.push(manifestDailyRoot
      ? comparedCheck(
        'daily', '04', 'Daily Root', dailyHex, manifestDailyRoot,
        '由所有 Category Root 重算的 dailyRoot 與 Daily Manifest 一致。',
        '由完整 Records 重算的 dailyRoot 與 Daily Manifest 不一致。',
      )
      : { id: 'daily', step: '04', label: 'Daily Root', status: 'fail', detail: 'Manifest 缺少 dailyRoot。', actual: dailyHex })
  }

  const body = manifestBody(manifest)
  const calculatedManifestHash = bytesToHex(await sha256(jcs(body)))
  const expectedManifestHash = typeof manifest.manifestHash === 'string' ? manifest.manifestHash : ''
  checks.push(expectedManifestHash
    ? comparedCheck(
      'manifest-hash', '05', 'Manifest Hash', calculatedManifestHash, expectedManifestHash,
      '移除 manifestHash 與 signatures 後重算結果一致。',
      'Manifest 內容可能遭修改，重算結果不一致。',
    )
    : { id: 'manifest-hash', step: '05', label: 'Manifest Hash', status: 'fail', detail: 'manifest 缺少 manifestHash。', actual: calculatedManifestHash })

  const signatures = Array.isArray(manifest.signatures) ? manifest.signatures : []
  const signatureEntry = signatures[0]
  if (signatureEntry && typeof signatureEntry === 'object' && !Array.isArray(signatureEntry)
      && typeof signatureEntry.signature === 'string' && certificate) {
    let fingerprintMatches = true
    let fingerprintActual: string | undefined
    const signatureFingerprint = signatureEntry.certificateFingerprint ?? signatureEntry.certFingerprint
    const expectedFingerprint = typeof signatureFingerprint === 'string' ? signatureFingerprint : certificate.fingerprint
    if (certificate.pem && expectedFingerprint) {
      fingerprintActual = bytesToHex(await sha256(certificateDer(certificate.pem)))
      fingerprintMatches = normalizeHash(fingerprintActual) === normalizeHash(expectedFingerprint)
    }
    const signatureValid = await verifyEs256(body, signatureEntry.signature, certificate)
    if (signatureValid === null) {
      checks.push({
        id: 'signature', step: '06', label: 'EMS ES256 簽章', status: 'pending',
        detail: fingerprintMatches
          ? '憑證指紋一致；此檔案未提供可供瀏覽器匯入的公鑰座標。'
          : '憑證指紋不一致，且尚無可匯入的公鑰座標。',
        actual: fingerprintActual, expected: expectedFingerprint,
      })
    } else {
      const valid = fingerprintMatches && signatureValid
      checks.push({
        id: 'signature', step: '06', label: 'EMS ES256 簽章', status: valid ? 'pass' : 'fail',
        detail: valid ? '憑證指紋與 ES256 簽章皆驗證成功。' : '憑證指紋或 ES256 簽章驗證失敗。',
        actual: fingerprintActual, expected: expectedFingerprint,
      })
    }
  } else {
    checks.push({ id: 'signature', step: '06', label: 'EMS ES256 簽章', status: 'pending', detail: '尚未提供 signatures 或 EMS 憑證資料。' })
  }

  checks.push({
    id: 'blockchain', step: '07', label: '鏈上 Anchor', status: 'pending',
    detail: '本機驗證完成後，將直接查詢 Sepolia 智慧合約。',
  })

  return { bundle, checks, manifestHash: calculatedManifestHash, dailyRoot: dailyHex }
}
