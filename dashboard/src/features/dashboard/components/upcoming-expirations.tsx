import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { statuses, severityToBadgeVariant } from '@/features/tasks/data/data'
import { useDashboardStats } from '../data/dashboard'

function formatDaysUntil(iso: string): { label: string; urgent: boolean } {
  const diffDays = Math.ceil(
    (new Date(iso).getTime() - Date.now()) / (1000 * 60 * 60 * 24)
  )
  if (diffDays === 0) return { label: 'Today', urgent: true }
  if (diffDays === 1) return { label: 'Tomorrow', urgent: true }
  if (diffDays <= 7) return { label: `${diffDays}d`, urgent: true }
  if (diffDays <= 30) return { label: `${diffDays}d`, urgent: false }
  const date = new Date(iso)
  return {
    label: date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    urgent: false,
  }
}

function formatLapsed(iso: string): string {
  // Include the year: these dates are in the past, so a bare "Mar 31" under an
  // "Expiring" heading reads as upcoming when it actually lapsed last year.
  return new Date(iso).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

export function UpcomingExpirations() {
  const { data, isLoading } = useDashboardStats()

  if (isLoading) {
    return (
      <div className='space-y-2'>
        {[1,2,3].map(i => (
          <div key={i} className='flex items-center justify-between rounded-md border px-3 py-2'>
            <div className='flex items-center gap-3'>
              <Skeleton className='h-3 w-10' />
              <Skeleton className='h-3 w-40' />
            </div>
            <Skeleton className='h-6 w-16 rounded-full' />
          </div>
        ))}
      </div>
    )
  }

  const upcoming = data?.upcomingExpirations ?? []
  const lapsed = data?.recentlyExpiredRateSheets ?? []

  // Fallback mode: nothing lapses within the next 90 days, so show the ones
  // that already have. Without this the card reads empty on every dataset whose
  // rate schedules are all in the past — which is what it was doing before.
  const showingLapsed = upcoming.length === 0 && lapsed.length > 0
  const items = showingLapsed ? lapsed : upcoming

  if (items.length === 0) {
    return (
      <div className='flex flex-col items-center justify-center py-8 gap-2 text-center'>
        <svg width='28' height='28' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='1.5' strokeLinecap='round' strokeLinejoin='round' className='text-muted-foreground/40'>
          <path d='M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z'/>
        </svg>
        <p className='text-sm text-muted-foreground'>No rate schedules expiring in the next 90 days.</p>
      </div>
    )
  }

  return (
    <div className='space-y-2'>
      {showingLapsed && (
        <p className='text-xs text-muted-foreground'>
          Nothing expiring in the next 90 days — showing the most recently lapsed.
        </p>
      )}
      {items.map((record) => {
        const statusDef = statuses.find((s) => s.value === record.status)
        const severity = statusDef?.severity ?? 'neutral'
        const badgeVariant = severityToBadgeVariant[severity]
        const { label: leadingLabel, urgent } = showingLapsed
          ? { label: formatLapsed(record.due_date), urgent: false }
          : formatDaysUntil(record.due_date)

        return (
          <div
            key={record.id}
            className='flex items-center justify-between rounded-md border px-3 py-2'
          >
            <div className='flex items-center gap-3 min-w-0'>
              <span
                className={
                  urgent
                    ? 'text-xs font-semibold text-destructive w-20 shrink-0 whitespace-nowrap'
                    : 'text-xs font-medium text-muted-foreground w-20 shrink-0 whitespace-nowrap'
                }
              >
                {leadingLabel}
              </span>
              <span className='text-sm font-medium truncate'>
                {record.title}
              </span>
            </div>
            <Badge variant={badgeVariant} className='ml-2 shrink-0'>
              {statusDef?.label ?? record.status}
            </Badge>
          </div>
        )
      })}
    </div>
  )
}
