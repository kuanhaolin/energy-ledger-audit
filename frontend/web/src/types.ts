export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue }

export type JsonObject = { [key: string]: JsonValue }
export type CheckStatus = 'idle' | 'pass' | 'fail' | 'pending'

export interface CertificateEntry {
  pem?: string
  publicKeyX?: string
  publicKeyY?: string
  fingerprint?: string
  [key: string]: JsonValue | undefined
}

export interface DailyRecordsInput {
  records: JsonObject[]
  manifest: JsonObject
  certificate?: CertificateEntry
  certificates?: CertificateEntry[]
  receipt?: JsonObject
  expected?: JsonObject
  [key: string]: unknown
}

export interface NormalizedDailyRecords {
  raw: DailyRecordsInput
  records: JsonObject[]
  record: JsonObject
  manifest: JsonObject
  certificate?: CertificateEntry
  receipt?: JsonObject
}

export interface VerificationCheck {
  id: string
  step: string
  label: string
  status: CheckStatus
  detail: string
  expected?: string
  actual?: string
}

export interface VerificationResult {
  bundle: NormalizedDailyRecords
  checks: VerificationCheck[]
  manifestHash?: string
  dailyRoot?: string
}
