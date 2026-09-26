import { useQuery } from '@tanstack/react-query'
import { supabase, PRODUCT_ID } from '@/lib/supabase'
import { toNumber } from '@/lib/format'
import { statuses } from '@/features/tasks/data/data'

export interface DashboardRecord {
  id: string
  title: string
  status: string
  created_at: string
}

export interface UpcomingRecord {
  id: string
  title: string
  status: string
  due_date: string
}

export interface DashboardStats {
  total: number
  needsAttention: number
  addedThisWeek: number
  addedPrevWeek: number
  totalPrevWeek: number
  needsAttentionPrevWeek: number
  statusCounts: { status: string; count: number }[]
  recent: DashboardRecord[]
  upcomingExpirations: UpcomingRecord[]
  recentlyExpiredRateSheets: UpcomingRecord[]
  // Rate schedules whose contracted rates have already lapsed. Kept separate
  // from needsAttention because an expired rate sheet is a different problem
  // from a flagged invoice, and it drives its own card.
  expiredCount: number
  // Money extracted from the invoices we processed. Rate sheets carry no
  // total, so they contribute nothing. This is what was reviewed, NOT what
  // was recovered — no invoice-vs-rate-sheet comparison has run, so there is
  // no overcharge figure to show.
  sumCharges: number
  recordsWithCharges: number
}

// Derived automatically from statuses with severity='critical' in data.tsx.
// No manual update needed — just set severity correctly per status there.
const ATTENTION_STATUSES = statuses
  .filter((s) => s.severity === 'critical')
  .map((s) => s.value.toLowerCase())

// 'expired:warning' is this product's rate-sheet expiry status (see data.tsx).
const EXPIRED_STATUS = 'expired:warning'

const UPCOMING_LIMIT = 10

interface RecordRow {
  id: string | number
  title: string | null
  status: string | null
  created_at: string
  due_date: string | null
  details: Record<string, unknown> | null
}

// A rate schedule's expiry is not the same thing as an invoice's payment due
// date. Rows processed after the processor fallback carry the expiry in
// `due_date`; rows written before it carry the expiry only inside `details`.
// Read both, otherwise the card matches nothing on the data we already have.
function expiryOf(row: RecordRow): string | null {
  const details: Record<string, unknown> = row.details ?? {}
  const raw =
    row.due_date ??
    (details.expiration_date as string | undefined) ??
    (details.rate_sheet_expiration_date as string | undefined)
  if (!raw) return null
  return String(raw).slice(0, 10)
}

function toDate(value: string | null): Date | null {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

// A rate sheet expands to one record per lane, so collapse those back to a
// single entry per sheet — an expiring 6-lane sheet shows once, not six times.
function summariseRateSheets(
  rows: RecordRow[],
  keep: (expiry: Date) => boolean,
  ascending: boolean
): UpcomingRecord[] {
  const candidates: { row: RecordRow; expiry: Date; key: string }[] = []

  for (const row of rows) {
    const iso = expiryOf(row)
    const expiry = toDate(iso)
    if (!expiry || !keep(expiry)) continue
    candidates.push({ row, expiry, key: `${row.title}|${iso}` })
  }

  candidates.sort((a, b) =>
    ascending
      ? a.expiry.getTime() - b.expiry.getTime()
      : b.expiry.getTime() - a.expiry.getTime()
  )

  const seen = new Set<string>()
  const out: UpcomingRecord[] = []
  for (const { row, expiry, key } of candidates) {
    if (seen.has(key)) continue
    seen.add(key)
    out.push({
      id: String(row.id),
      title: row.title ?? 'Untitled rate schedule',
      status: row.status ?? 'unknown',
      due_date: expiry.toISOString(),
    })
    if (out.length === UPCOMING_LIMIT) break
  }
  return out
}

async function fetchDashboardStats(): Promise<DashboardStats> {
  const { data, error } = await supabase
    .from('records')
    .select('id, title, status, created_at, due_date, details')
    .eq('product_id', PRODUCT_ID)
    .order('created_at', { ascending: false })

  if (error) throw error

  const rows = (data ?? []) as RecordRow[]
  const now = new Date()
  const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000)
  const twoWeeksAgo = new Date(now.getTime() - 14 * 24 * 60 * 60 * 1000)
  const in90Days = new Date(now.getTime() + 90 * 24 * 60 * 60 * 1000)

  const statusMap = new Map<string, number>()
  let needsAttention = 0
  let addedThisWeek = 0
  let addedPrevWeek = 0
  let needsAttentionPrevWeek = 0
  let expiredCount = 0
  let sumCharges = 0
  let recordsWithCharges = 0

  for (const row of rows) {
    const status = row.status ?? 'unknown'
    statusMap.set(status, (statusMap.get(status) ?? 0) + 1)
    const createdAt = new Date(row.created_at)
    const isAttention = ATTENTION_STATUSES.includes(status.toLowerCase())

    if (isAttention) needsAttention += 1
    if (status.toLowerCase() === EXPIRED_STATUS) expiredCount += 1
    if (createdAt >= weekAgo) {
      addedThisWeek += 1
    } else if (createdAt >= twoWeeksAgo) {
      addedPrevWeek += 1
      if (isAttention) needsAttentionPrevWeek += 1
    }

    const charges = toNumber(row.details?.total_charges)
    if (charges !== null) {
      sumCharges += charges
      recordsWithCharges += 1
    }
  }
  const totalPrevWeek = rows.filter((r) => new Date(r.created_at) < weekAgo).length

  // Rate schedules only: an invoice's due_date is a *payment* due date, so
  // listing both under one "Expirations" heading would mislead.
  const rateSheets = rows.filter(
    (r) => (r.details?.document_type as string | undefined) === 'rate_sheet'
  )

  const upcomingExpirations = summariseRateSheets(
    rateSheets,
    (expiry) => expiry >= now && expiry <= in90Days,
    true
  )

  // Nothing lapses in the next 90 days. Rather than render an empty card, show
  // the most recently lapsed schedules — still true, still worth knowing.
  const recentlyExpiredRateSheets =
    upcomingExpirations.length === 0
      ? summariseRateSheets(rateSheets, (expiry) => expiry < now, false)
      : []

  return {
    total: rows.length,
    needsAttention,
    addedThisWeek,
    addedPrevWeek,
    totalPrevWeek,
    needsAttentionPrevWeek,
    statusCounts: Array.from(statusMap.entries()).map(([status, count]) => ({
      status,
      count,
    })),
    recent: rows.slice(0, 5).map((row) => ({
      id: String(row.id),
      title: row.title ?? '',
      status: row.status ?? 'unknown',
      created_at: row.created_at,
    })),
    upcomingExpirations,
    recentlyExpiredRateSheets,
    expiredCount,
    sumCharges,
    recordsWithCharges,
  }
}

export function useDashboardStats() {
  return useQuery({
    queryKey: ['dashboard-stats', PRODUCT_ID],
    queryFn: fetchDashboardStats,
  })
}
