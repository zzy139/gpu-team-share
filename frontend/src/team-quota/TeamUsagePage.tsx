// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {
  Alert,
  Badge,
  BarChart,
  BarChartProps,
  Box,
  ColumnLayout,
  Container,
  ContentLayout,
  DatePicker,
  ExpandableSection,
  FormField,
  Header,
  SegmentedControl,
  SpaceBetween,
  Table,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {UserIdentity} from '../auth/types'
import EmptyState from '../components/EmptyState'
import {ValueWithLabel} from '../components/ValueWithLabel'
import {toShortDollarAmount} from '../old-pages/Clusters/Costs/valueFormatter'
import Layout from '../old-pages/Layout'
import {useState} from '../store'
import {errorMessage} from './api'
import ClusterSelect, {useSelectedTeamTarget} from './ClusterSelect'
import {formatDollars, formatNumber, formatPercent} from './format'
import {computePeriod, PeriodKind} from './period'
import {useTeamTrendQuery, useTeamUsageQuery} from './queries'
import {isAdmin} from './roles'
import PageNotifications from './PageNotifications'
import {TeamBreadcrumbs} from './TeamsPage'
import {
  MemberUsage,
  TeamTrendResponse,
  TeamUsage,
  TypeUsageFigures,
  UsageFigures,
  UsageTotals,
} from './types'

const TREND_MONTHS = 6
type TrendMetric = 'cost' | 'gpuHours'

const NOTE_KEYS = {
  'billed-unavailable': 'teamUsage.notes.billed-unavailable',
  'billed-cost-explorer-not-active':
    'teamUsage.notes.billed-cost-explorer-not-active',
  'billed-future-period': 'teamUsage.notes.billed-future-period',
  'billed-unallocated-no-usage': 'teamUsage.notes.billed-unallocated-no-usage',
  'estimate-prices-unavailable': 'teamUsage.notes.estimate-prices-unavailable',
  'estimate-has-unpriced-hours': 'teamUsage.notes.estimate-has-unpriced-hours',
} as const
type NoteKey = keyof typeof NOTE_KEYS

function knownNotes(notes: string[]): NoteKey[] {
  return notes.filter((note): note is NoteKey => note in NOTE_KEYS)
}

function LabelWithBadge({
  label,
  kind,
}: {
  label: string
  kind: 'billed' | 'estimate'
}) {
  const {t} = useTranslation()
  return (
    <SpaceBetween direction="horizontal" size="xxs">
      <span>{label}</span>
      <Badge color={kind === 'estimate' ? 'blue' : 'grey'}>
        {kind === 'estimate'
          ? t('teamUsage.labels.estimate')
          : t('teamUsage.labels.billed')}
      </Badge>
    </SpaceBetween>
  )
}

function billedTotal(team: TeamUsage) {
  if (team.compute_cost === null || team.shared_cost === null) return null
  return team.compute_cost + team.shared_cost
}

function EstimateCell({row}: {row: UsageFigures}) {
  const {t} = useTranslation()
  return (
    <>
      {row.unpriced_instance_hours > 0
        ? t('teamUsage.values.estimateWithUnpriced', {
            cost: formatDollars(row.estimate_cost),
            hours: formatNumber(row.unpriced_instance_hours),
          })
        : formatDollars(row.estimate_cost)}
    </>
  )
}
interface TypeRow extends TypeUsageFigures {
  key: string
  team: string
}
function ByTypeTable({teams}: {teams: TeamUsage[]}) {
  const {t} = useTranslation()
  const rows: TypeRow[] = teams.flatMap(team =>
    (team.by_type || []).map(entry => ({
      ...entry,
      key: `${team.name}/${entry.instance_type}`,
      team: team.name,
    })),
  )
  return (
    <Table<TypeRow>
      variant="container"
      trackBy="key"
      items={rows}
      header={
        <Header variant="h2" description={t('teamUsage.byType.description')}>
          {t('teamUsage.byType.title')}
        </Header>
      }
      empty={<Box textAlign="center">{t('teamUsage.empty.subtitle')}</Box>}
      columnDefinitions={[
        {id: 'team', header: t('teamUsage.columns.team'), cell: r => r.team},
        {
          id: 'type',
          header: t('teamUsage.columns.instanceType'),
          cell: r => r.instance_type,
        },
        {
          id: 'instanceHours',
          header: t('teamUsage.columns.instanceHours'),
          cell: r => formatNumber(r.instance_hours),
        },
        {
          id: 'gpuHours',
          header: t('teamUsage.columns.gpuHours'),
          cell: r => formatNumber(r.gpu_hours),
        },
        {
          id: 'estimate',
          header: (
            <LabelWithBadge
              label={t('teamUsage.columns.estimate')}
              kind="estimate"
            />
          ),
          cell: r => <EstimateCell row={r} />,
        },
      ]}
    />
  )
}
interface MemberRow extends MemberUsage {
  key: string
}
function ByMemberTable({members}: {members: MemberUsage[]}) {
  const {t} = useTranslation()
  const rows: MemberRow[] = members.map(m => ({
    ...m,
    key: `${m.team}/${m.user}`,
  }))
  return (
    <Table<MemberRow>
      variant="container"
      trackBy="key"
      items={rows}
      header={
        <Header variant="h2" description={t('teamUsage.byMember.description')}>
          {t('teamUsage.byMember.title')}
        </Header>
      }
      empty={<Box textAlign="center">{t('teamUsage.empty.subtitle')}</Box>}
      columnDefinitions={[
        {id: 'team', header: t('teamUsage.columns.team'), cell: r => r.team},
        {id: 'user', header: t('teamUsage.columns.user'), cell: r => r.user},
        {
          id: 'types',
          header: t('teamUsage.columns.instanceTypes'),
          cell: r =>
            (r.by_type || [])
              .map(e =>
                t('teamUsage.values.typeHours', {
                  type: e.instance_type,
                  hours: formatNumber(e.instance_hours),
                }),
              )
              .join(', ') || '-',
        },
        {
          id: 'instanceHours',
          header: t('teamUsage.columns.instanceHours'),
          cell: r => formatNumber(r.instance_hours),
        },
        {
          id: 'gpuHours',
          header: t('teamUsage.columns.gpuHours'),
          cell: r => formatNumber(r.gpu_hours),
        },
        {
          id: 'estimate',
          header: (
            <LabelWithBadge
              label={t('teamUsage.columns.estimate')}
              kind="estimate"
            />
          ),
          cell: r => <EstimateCell row={r} />,
        },
      ]}
    />
  )
}
function SharedCosts({totals}: {totals: UsageTotals}) {
  const {t} = useTranslation()
  return (
    <Container
      header={
        <Header variant="h2" description={t('teamUsage.shared.description')}>
          {t('teamUsage.shared.title')}
        </Header>
      }
    >
      <ColumnLayout columns={4} variant="text-grid">
        <ValueWithLabel label={t('teamUsage.shared.compute')}>
          {formatDollars(totals.compute_cost)}
        </ValueWithLabel>
        <ValueWithLabel label={t('teamUsage.shared.shared')}>
          {formatDollars(totals.shared_cost)}
        </ValueWithLabel>
        <ValueWithLabel label={t('teamUsage.shared.total')}>
          {formatDollars(totals.total_cost)}
        </ValueWithLabel>
        <ValueWithLabel label={t('teamUsage.shared.unallocated')}>
          {formatDollars(
            totals.unallocated_compute_cost === null ||
              totals.unallocated_shared_cost === null
              ? null
              : totals.unallocated_compute_cost +
                  totals.unallocated_shared_cost,
          )}
        </ValueWithLabel>
        <ValueWithLabel label={t('teamUsage.shared.instanceHours')}>
          {formatNumber(totals.instance_hours)}
        </ValueWithLabel>
        <ValueWithLabel label={t('teamUsage.shared.gpuHours')}>
          {formatNumber(totals.gpu_hours)}
        </ValueWithLabel>
        <ValueWithLabel label={t('teamUsage.shared.estimate')}>
          {formatDollars(totals.estimate_cost)}
        </ValueWithLabel>
      </ColumnLayout>
    </Container>
  )
}

function toTrendSeries(
  data: TeamTrendResponse | undefined,
  metric: TrendMetric,
  formatter: (value: number) => string,
): BarChartProps<string>['series'] {
  if (!data) return []
  const teams = Array.from(
    new Set(data.months.flatMap(month => month.teams.map(team => team.name))),
  ).sort()
  return teams.map(name => ({
    title: name,
    type: 'bar' as const,
    valueFormatter: formatter,
    data: data.months.map(month => {
      const team = month.teams.find(item => item.name === name)
      const y =
        metric === 'cost'
          ? (team?.compute_cost ?? 0) + (team?.shared_cost ?? 0)
          : team?.gpu_hours ?? 0
      return {x: month.month, y}
    }),
  }))
}

function Trend({admin, team}: {admin: boolean; team?: string}) {
  const {t} = useTranslation()
  const target = useSelectedTeamTarget()
  const [metric, setMetric] = React.useState<TrendMetric>('cost')
  const {data, isLoading, isError, refetch} = useTeamTrendQuery(
    target,
    TREND_MONTHS,
    team,
  )
  const formatter = React.useCallback(
    (value: number) =>
      metric === 'cost' ? formatDollars(value) : formatNumber(value),
    [metric],
  )
  const series = React.useMemo(
    () => toTrendSeries(data, metric, formatter),
    [data, metric, formatter],
  )
  const allZero = !data?.months.some(month =>
    month.teams.some(team =>
      metric === 'cost'
        ? (team.compute_cost ?? 0) + (team.shared_cost ?? 0) !== 0
        : team.gpu_hours !== 0,
    ),
  )
  return (
    <Container
      header={
        <Header
          variant="h2"
          description={t(
            admin && !team
              ? 'teamUsage.trend.description'
              : 'teamUsage.trend.leadDescription',
          )}
          actions={
            <SegmentedControl
              selectedId={metric}
              onChange={({detail}) =>
                setMetric(detail.selectedId as TrendMetric)
              }
              options={[
                {id: 'cost', text: t('teamUsage.trend.billedCost')},
                {id: 'gpuHours', text: t('teamUsage.trend.gpuHours')},
              ]}
            />
          }
        >
          {t('teamUsage.trend.title')}
        </Header>
      }
    >
      {data && !data.billed_available && metric === 'cost' && (
        <Box margin={{bottom: 's'}}>
          <Alert type="warning">
            {t('teamUsage.notes.billed-unavailable')}
          </Alert>
        </Box>
      )}
      <BarChart
        hideFilter
        stackedBars
        series={allZero ? [] : series}
        xScaleType="categorical"
        xDomain={data?.months.map(month => month.month)}
        xTitle={t('teamUsage.trend.xTitle')}
        yTitle={
          metric === 'cost'
            ? t('teamUsage.trend.yTitleCost')
            : t('teamUsage.trend.yTitleGpuHours')
        }
        height={300}
        statusType={isLoading ? 'loading' : isError ? 'error' : 'finished'}
        loadingText={t('teamUsage.trend.loading')}
        errorText={t('teamUsage.trend.error')}
        recoveryText={t('teamUsage.trend.retry')}
        onRecoveryClick={() => refetch()}
        i18nStrings={{
          yTickFormatter: metric === 'cost' ? toShortDollarAmount : undefined,
        }}
        empty={
          <EmptyState
            title={t('teamUsage.trend.empty.title')}
            subtitle={t('teamUsage.trend.empty.subtitle')}
          />
        }
      />
    </Container>
  )
}

/** 费用说明: where billed and estimated figures come from, with a worked example. */
export function CostExplanation() {
  const {t} = useTranslation()
  return (
    <ExpandableSection
      variant="container"
      headerText={t('teamUsage.explain.title')}
    >
      <SpaceBetween size="s">
        <Box>{t('teamUsage.explain.billed')}</Box>
        <Box>{t('teamUsage.explain.estimate')}</Box>
        <Box>{t('teamUsage.explain.allocation')}</Box>
        <Box>{t('teamUsage.explain.example')}</Box>
      </SpaceBetween>
    </ExpandableSection>
  )
}

/**
 * Usage and cost tables, the explanation and the trend. `team` limits everything to one team (the 费用 tab of the
 * team page, where the cluster is chosen by the page); without it the cluster select is shown here.
 */
export function TeamUsageContent({team}: {team?: string}) {
  const {t} = useTranslation()
  const identity: UserIdentity | null = useState(['identity'])
  const admin = isAdmin(identity)
  const target = useSelectedTeamTarget()
  const [kind, setKind] = React.useState<PeriodKind>('thisMonth')
  const [customStart, setCustomStart] = React.useState('')
  const [customEnd, setCustomEnd] = React.useState('')
  const period = React.useMemo(
    () =>
      computePeriod(kind, new Date(), {
        startDate: customStart,
        endDate: customEnd,
      }),
    [kind, customStart, customEnd],
  )
  const {data, isLoading, isError, error} = useTeamUsageQuery(
    target,
    period?.start || '',
    period?.end || '',
    team,
  )

  const columns = [
    {
      id: 'name',
      header: t('teamUsage.columns.team'),
      cell: (team: TeamUsage) => team.name,
    },
    {
      id: 'instanceHours',
      header: t('teamUsage.columns.instanceHours'),
      cell: (team: TeamUsage) => formatNumber(team.instance_hours),
    },
    {
      id: 'gpuHours',
      header: t('teamUsage.columns.gpuHours'),
      cell: (team: TeamUsage) => formatNumber(team.gpu_hours),
    },
    {
      id: 'share',
      header: t('teamUsage.columns.share'),
      cell: (team: TeamUsage) => formatPercent(team.cost_share),
    },
    {
      id: 'billedCompute',
      header: (
        <LabelWithBadge
          label={t('teamUsage.columns.billedCompute')}
          kind="billed"
        />
      ),
      cell: (team: TeamUsage) => formatDollars(team.compute_cost),
    },
    {
      id: 'billedShared',
      header: (
        <LabelWithBadge
          label={t('teamUsage.columns.billedShared')}
          kind="billed"
        />
      ),
      cell: (team: TeamUsage) => formatDollars(team.shared_cost),
    },
    {
      id: 'billedTotal',
      header: (
        <LabelWithBadge
          label={t('teamUsage.columns.billedTotal')}
          kind="billed"
        />
      ),
      cell: (team: TeamUsage) => formatDollars(billedTotal(team)),
    },
    {
      id: 'estimate',
      header: (
        <LabelWithBadge
          label={t('teamUsage.columns.estimate')}
          kind="estimate"
        />
      ),
      cell: (team: TeamUsage) => <EstimateCell row={team} />,
    },
  ]

  const priceList = Object.entries(data?.prices || {})
    .map(([type, price]) => `${type} $${price}`)
    .join(', ')

  const teams = data?.teams || []
  return (
    <SpaceBetween size="l">
      {isError && (
        <Alert type="error" header={t('teamUsage.loadError')}>
          {errorMessage(error)}
        </Alert>
      )}
      {data && knownNotes(data.notes).length > 0 && (
        <Alert type="warning" header={t('teamUsage.notes.title')}>
          <ul>
            {knownNotes(data.notes).map(note => (
              <li key={note}>{t(NOTE_KEYS[note])}</li>
            ))}
          </ul>
        </Alert>
      )}
      <CostExplanation />
      <Container>
        <ColumnLayout columns={team ? 1 : 2}>
          {!team && <ClusterSelect />}
          <FormField label={t('teamUsage.period.label')}>
            <SpaceBetween size="xs">
              <SegmentedControl
                selectedId={kind}
                onChange={({detail}) =>
                  setKind(detail.selectedId as PeriodKind)
                }
                options={[
                  {id: 'thisMonth', text: t('teamUsage.period.thisMonth')},
                  {id: 'lastMonth', text: t('teamUsage.period.lastMonth')},
                  {
                    id: 'last30Days',
                    text: t('teamUsage.period.last30Days'),
                  },
                  {id: 'custom', text: t('teamUsage.period.custom')},
                ]}
              />
              {kind === 'custom' && (
                <SpaceBetween direction="horizontal" size="xs">
                  <DatePicker
                    value={customStart}
                    onChange={({detail}) => setCustomStart(detail.value)}
                    placeholder={t('teamUsage.period.startPlaceholder')}
                    ariaLabel={t('teamUsage.period.start')}
                    todayAriaLabel={t('teamUsage.period.today')}
                    nextMonthAriaLabel={t('teamUsage.period.nextMonth')}
                    previousMonthAriaLabel={t('teamUsage.period.previousMonth')}
                  />
                  <DatePicker
                    value={customEnd}
                    onChange={({detail}) => setCustomEnd(detail.value)}
                    placeholder={t('teamUsage.period.endPlaceholder')}
                    ariaLabel={t('teamUsage.period.end')}
                    todayAriaLabel={t('teamUsage.period.today')}
                    nextMonthAriaLabel={t('teamUsage.period.nextMonth')}
                    previousMonthAriaLabel={t('teamUsage.period.previousMonth')}
                  />
                </SpaceBetween>
              )}
            </SpaceBetween>
          </FormField>
        </ColumnLayout>
      </Container>
      <Table
        variant="container"
        trackBy="name"
        loading={!!target && !!period && isLoading}
        loadingText={t('teamUsage.loading')}
        items={teams}
        columnDefinitions={columns}
        header={
          <Header
            variant="h2"
            description={
              data
                ? t('teamUsage.table.period', {
                    start: data.period.start,
                    end: data.period.end,
                  })
                : undefined
            }
          >
            {t('teamUsage.table.title')}
          </Header>
        }
        footer={
          priceList ? (
            <Box variant="small">
              {t('teamUsage.table.prices', {prices: priceList})}
            </Box>
          ) : undefined
        }
        empty={
          <EmptyState
            title={t('teamUsage.empty.title')}
            subtitle={t('teamUsage.empty.subtitle')}
          />
        }
      />
      {data && <ByTypeTable teams={teams} />}
      {data && <ByMemberTable members={data.by_member || []} />}
      {admin && !team && data?.totals && <SharedCosts totals={data.totals} />}
      <Trend admin={admin} team={team} />
    </SpaceBetween>
  )
}

/** 费用总览: every team for admins, the own team(s) for leads. */
export default function TeamUsagePage() {
  const {t} = useTranslation()
  return (
    <Layout
      contentType="default"
      breadcrumbs={
        <TeamBreadcrumbs titleKey="global.menu.teamUsage" href="/team-usage" />
      }
      notifications={<PageNotifications />}
    >
      <ContentLayout
        header={
          <Header variant="h1" description={t('teamUsage.header.description')}>
            {t('teamUsage.header.title')}
          </Header>
        }
      >
        <TeamUsageContent />
      </ContentLayout>
    </Layout>
  )
}
