import { useEffect, useState } from 'react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { Search } from '@/components/search'
import { ThemeSwitch } from '@/components/theme-switch'
import { JobsCard } from '@/features/jobs/components/jobs-card'
import { NotificationsBell } from '@/components/notifications-bell'
import { PRODUCT_ARCHETYPE, RECORDS_LABEL } from '@/product-config'
import { formatCurrency } from '@/lib/format'
import { ReportCard } from './components/report-card'
import { Overview } from './components/overview'
import { RecentActivity } from './components/recent-activity'
import { UpcomingExpirations } from './components/upcoming-expirations'
import { useDashboardStats } from './data/dashboard'
import { supabase } from '@/lib/supabase'
import { Skeleton } from '@/components/ui/skeleton'
import { NumberTicker } from '@/components/magicui/number-ticker'

function Trend({ current, previous }: { current: number; previous: number }) {
  if (previous === 0 && current === 0) return null
  const diff = current - previous
  const pct = previous === 0 ? 100 : Math.round(Math.abs(diff / previous) * 100)
  if (diff === 0) return <span className='text-xs text-muted-foreground'>No change</span>
  return (
    <span className={diff > 0 ? 'text-xs text-success' : 'text-xs text-destructive'}>
      {diff > 0 ? '↑' : '↓'} {pct}% vs last week
    </span>
  )
}

// PRODUCT_CUSTOMIZE: these four cards are written for a document-audit
// product — volume, what needs a human, what has lapsed, and the money
// reviewed. Retitle to match the domain if the product tracks something else.
export function Dashboard() {
  const { data, isLoading } = useDashboardStats()
  const [showUpgradeBanner, setShowUpgradeBanner] = useState(false)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('upgraded') === 'true') {
      window.history.replaceState({}, '', window.location.pathname)
      setTimeout(() => setShowUpgradeBanner(true), 0)
    }
  }, [])

  async function handleRefreshSession() {
    await supabase.auth.refreshSession()
    window.location.reload()
  }

  const chargesLabel = data ? formatCurrency(data.sumCharges) : null
  const chargedRecords = data?.recordsWithCharges ?? 0

  return (
    <>
      {/* ===== Top Heading ===== */}
      <Header>
        <Search />
        <ThemeSwitch />
        <NotificationsBell />
        <ProfileDropdown />
      </Header>

      {/* ===== Main ===== */}
      <Main>
        {showUpgradeBanner && (
          <div className='flex items-center justify-between rounded-lg border border-success bg-success/10 px-4 py-3 text-sm text-success mb-4'>
            <span>Payment successful! Refresh your session to activate full access.</span>
            <button
              onClick={handleRefreshSession}
              className='ml-4 font-medium underline underline-offset-2 hover:no-underline'
            >
              Refresh now
            </button>
          </div>
        )}
        <div className='mb-2 flex items-center justify-between space-y-2'>
          <h1 className='text-2xl font-bold tracking-tight'>Dashboard</h1>
        </div>
        <div className='space-y-4'>
          <JobsCard />
          {PRODUCT_ARCHETYPE === 'report' && <ReportCard />}
          <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
            <Card>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-2'>
                <CardTitle className='text-sm font-medium'>{RECORDS_LABEL}</CardTitle>
              </CardHeader>
              <CardContent>
                {isLoading ? (
                  <Skeleton className='h-8 w-16' />
                ) : (
                  <>
                    <div className='text-2xl font-bold tracking-tight'>
                      <NumberTicker value={data?.total ?? 0} />
                    </div>
                    <Trend current={data?.total ?? 0} previous={data?.totalPrevWeek ?? 0} />
                  </>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-2'>
                <CardTitle className='text-sm font-medium'>Flagged for Review</CardTitle>
              </CardHeader>
              <CardContent>
                {isLoading ? (
                  <Skeleton className='h-8 w-16' />
                ) : (
                  <>
                    <div className='text-2xl font-bold tracking-tight text-destructive'>
                      <NumberTicker value={data?.needsAttention ?? 0} />
                    </div>
                    <p className='text-xs text-muted-foreground'>
                      Errors or overbilling detected
                    </p>
                  </>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-2'>
                <CardTitle className='text-sm font-medium'>Rate Agreements Expired</CardTitle>
              </CardHeader>
              <CardContent>
                {isLoading ? (
                  <Skeleton className='h-8 w-16' />
                ) : (
                  <>
                    <div className='text-2xl font-bold tracking-tight text-warning'>
                      <NumberTicker value={data?.expiredCount ?? 0} />
                    </div>
                    <p className='text-xs text-muted-foreground'>
                      Audits run against stale rates
                    </p>
                  </>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-2'>
                <CardTitle className='text-sm font-medium'>Charges Reviewed</CardTitle>
              </CardHeader>
              <CardContent>
                {isLoading ? (
                  <Skeleton className='h-8 w-24' />
                ) : (
                  <>
                    <div className='text-2xl font-bold tracking-tight'>
                      {chargesLabel ?? '—'}
                    </div>
                    <p className='text-xs text-muted-foreground'>
                      {chargedRecords === 1
                        ? 'across 1 invoice'
                        : `across ${chargedRecords} invoices`}
                    </p>
                  </>
                )}
              </CardContent>
            </Card>
          </div>
          <div className='grid grid-cols-1 gap-4 lg:grid-cols-7'>
            <Card className='col-span-1 lg:col-span-4'>
              <CardHeader>
                <CardTitle>Status Breakdown</CardTitle>
              </CardHeader>
              <CardContent className='ps-2'>
                <Overview />
              </CardContent>
            </Card>
            <Card className='col-span-1 lg:col-span-3'>
              <CardHeader>
                <CardTitle>Recent Activity</CardTitle>
                <CardDescription>Latest records added</CardDescription>
              </CardHeader>
              <CardContent>
                <RecentActivity />
              </CardContent>
            </Card>
          </div>
          {/* PRODUCT_CUSTOMIZE: this card only suits products whose records carry
              an expiry/renewal/deadline. Here it is scoped to rate schedules —
              an invoice's due_date is a payment due date, not an expiry — and
              falls back to recently lapsed schedules so it is never empty on a
              dataset whose sheets are all in the past. Remove the card for
              products that have no such dates. */}
          <Card>
            <CardHeader>
              <CardTitle>Rate Agreements Expiring</CardTitle>
              <CardDescription>Carrier rate schedules expiring soon — or already lapsed</CardDescription>
            </CardHeader>
            <CardContent>
              <UpcomingExpirations />
            </CardContent>
          </Card>
        </div>
      </Main>
    </>
  )
}
