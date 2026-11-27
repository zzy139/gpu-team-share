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
  Box,
  Button,
  ContentLayout,
  Header,
  SpaceBetween,
  Table,
  Tabs,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {Navigate, useNavigate, useParams} from 'react-router-dom'
import {UserIdentity} from '../auth/types'
import Layout from '../old-pages/Layout'
import {useState} from '../store'
import {errorMessage} from './api'
import {CapacityStatusBadge} from './CapacityStatus'
import ClusterSelect, {useSelectedTeamTarget} from './ClusterSelect'
import {formatSpec} from './format'
import {LeadTeamSelect, useLeadTarget} from './lead/LeadTeam'
import {QuotaHoldersContent} from './lead/QuotaHoldersPage'
import {TeamJobsContent} from './lead/TeamJobsPage'
import {EnvsContent} from './member/EnvsPage'
import {MemberTarget} from './member/types'
import MembersModal from './MembersModal'
import PageNotifications from './PageNotifications'
import {
  useCapacityQuery,
  useLifecycleTeamsQuery,
  useTeamsQuery,
} from './queries'
import {isAdmin} from './roles'
import {TeamBreadcrumbs} from './TeamsPage'
import {TeamUsageContent} from './TeamUsagePage'
import {CapacityStatus, CatalogType, OffboardState, TeamTarget} from './types'

export const TEAM_TABS = [
  'overview',
  'members',
  'jobs',
  'cost',
  'envs',
] as const
export type TeamTab = typeof TEAM_TABS[number]

export function toTeamTab(
  value: string | undefined,
  tabs: readonly TeamTab[] = TEAM_TABS,
): TeamTab {
  return (tabs as readonly string[]).includes(value || '')
    ? (value as TeamTab)
    : 'overview'
}

/** One row of 机型与配额: an instance type with its specs, the team's quota and use, and the capacity status. */
export interface QuotaOverviewRow {
  entry: CatalogType
  limit: number
  running: number
  remaining: number
  status: CapacityStatus | null | undefined
}

export function QuotaOverviewTable({
  rows,
  loading,
}: {
  rows: QuotaOverviewRow[]
  loading: boolean
}) {
  const {t} = useTranslation()
  return (
    <Table<QuotaOverviewRow>
      variant="container"
      trackBy={row => row.entry.instance_type}
      loading={loading}
      loadingText={t('teamQuota.member.loading')}
      items={rows}
      empty={<Box textAlign="center">{t('teamQuota.typeLimits.empty')}</Box>}
      header={
        <Header
          variant="h2"
          description={t('teamQuota.detail.overviewDescription')}
        >
          {t('teamQuota.detail.overviewTitle')}
        </Header>
      }
      columnDefinitions={[
        {
          id: 'type',
          header: t('teamQuota.capacity.columns.instanceType'),
          cell: row => row.entry.instance_type,
        },
        {
          id: 'spec',
          header: t('teamQuota.capacity.columns.spec'),
          cell: row => formatSpec(row.entry),
        },
        {
          id: 'limit',
          header: t('teamQuota.capacity.columns.limit'),
          cell: row =>
            row.limit ? row.limit : t('teamQuota.values.notAllowed'),
        },
        {
          id: 'running',
          header: t('teamQuota.capacity.columns.running'),
          cell: row => row.running,
        },
        {
          id: 'remaining',
          header: t('teamQuota.capacity.columns.remaining'),
          cell: row => (row.limit ? row.remaining : '-'),
        },
        {
          id: 'status',
          header: t('teamQuota.capacity.columns.status'),
          cell: row => <CapacityStatusBadge status={row.status} />,
        },
      ]}
    />
  )
}

function TeamOverview({
  cluster,
  team,
}: {
  cluster: TeamTarget | null
  team: string
}) {
  const {t} = useTranslation()
  const {data, isLoading, isError, error} = useCapacityQuery(cluster, team)
  const rows = (data?.types || []).map<QuotaOverviewRow>(entry => {
    const own = entry.teams.find(row => row.team === team)
    const limit = own?.limit || 0
    const running = own?.running || 0
    return {
      entry,
      limit,
      running,
      remaining: Math.max(limit - running, 0),
      status: entry.status,
    }
  })
  return (
    <SpaceBetween size="m">
      {isError && (
        <Alert type="error" header={t('teamQuota.capacity.loadError')}>
          {errorMessage(error)}
        </Alert>
      )}
      <QuotaOverviewTable rows={rows} loading={!!cluster && isLoading} />
    </SpaceBetween>
  )
}

interface MemberRow {
  user: string
  state: OffboardState | 'active'
}

function TeamMembers({
  cluster,
  target,
}: {
  cluster: TeamTarget | null
  target: MemberTarget
}) {
  const {t} = useTranslation()
  const identity: UserIdentity | null = useState(['identity'])
  const admin = isAdmin(identity)
  const {data, isLoading, isError, error} = useTeamsQuery(cluster)
  const lifecycle = useLifecycleTeamsQuery(cluster, admin).data
  const [managing, setManaging] = React.useState(false)
  const team = data?.teams.find(row => row.name === target.team) || null
  const editable = admin && !!data?.editable && !!team
  const managed = (lifecycle?.records || []).some(
    record => record.team === target.team && record.state === 'active',
  )
  const offboarded = team?.offboarded || {}
  const rows: MemberRow[] = [
    ...(team?.members || []),
    ...Object.keys(offboarded)
      .filter(user => !team?.members.includes(user))
      .sort(),
  ].map(user => ({user, state: offboarded[user] || 'active'}))
  return (
    <SpaceBetween size="m">
      {isError && (
        <Alert type="error" header={t('teamQuota.detail.loadError')}>
          {errorMessage(error)}
        </Alert>
      )}
      <Table<MemberRow>
        variant="container"
        trackBy="user"
        loading={!!cluster && isLoading}
        loadingText={t('teamQuota.loading')}
        items={rows}
        empty={<Box textAlign="center">{t('teamQuota.members.empty')}</Box>}
        header={
          <Header
            variant="h2"
            counter={`(${rows.length})`}
            description={t(
              editable
                ? 'teamQuota.detail.membersDescription'
                : 'teamQuota.detail.membersReadOnly',
            )}
            actions={
              editable ? (
                <Button onClick={() => setManaging(true)}>
                  {t('teamQuota.actions.manageMembers')}
                </Button>
              ) : undefined
            }
          >
            {t('teamQuota.detail.membersTitle', {team: target.team})}
          </Header>
        }
        columnDefinitions={[
          {
            id: 'user',
            header: t('teamQuota.members.user'),
            cell: row => row.user,
          },
          {
            id: 'state',
            header: t('teamQuota.members.state'),
            cell: row => t(`teamQuota.offboard.states.${row.state}`),
          },
        ]}
      />
      <QuotaHoldersContent target={target} />
      {editable && (
        <MembersModal
          team={team}
          target={cluster}
          visible={managing}
          onDismiss={() => setManaging(false)}
          managed={managed}
          emailDomains={lifecycle?.email_domains || []}
        />
      )}
    </SpaceBetween>
  )
}

/**
 * Team page tabs 概览 / 成员 / 作业 / 费用 / 环境 of one team on the selected cluster. Each tab has its own URL
 * (`<basePath>/<tab>`; 概览 is `basePath`), so a link or a reload opens the same tab.
 */
export function TeamDetailContent({
  cluster,
  team,
  tab,
  basePath,
}: {
  cluster: TeamTarget | null
  team: string
  tab: TeamTab
  basePath: string
}) {
  const {t} = useTranslation()
  const navigate = useNavigate()
  const target = React.useMemo<MemberTarget | null>(
    () =>
      cluster
        ? {clusterName: cluster.clusterName, region: cluster.region, team}
        : null,
    [cluster, team],
  )
  const content = (id: TeamTab) => {
    if (!target) return null
    switch (id) {
      case 'overview':
        return <TeamOverview cluster={cluster} team={team} />
      case 'members':
        return <TeamMembers cluster={cluster} target={target} />
      case 'jobs':
        return <TeamJobsContent target={target} />
      case 'cost':
        return <TeamUsageContent team={team} />
      case 'envs':
        return <EnvsContent target={target} />
    }
  }
  return (
    <Tabs
      activeTabId={tab}
      onChange={({detail}) =>
        navigate(
          detail.activeTabId === 'overview'
            ? basePath
            : `${basePath}/${detail.activeTabId}`,
        )
      }
      tabs={TEAM_TABS.map(id => ({
        id,
        label: t(`teamQuota.detail.tabs.${id}`),
        content: id === tab ? content(id) : null,
      }))}
    />
  )
}

/** Admin: /teams/:team/:tab, opened from the 团队管理 list. */
export function TeamDetailPage() {
  const {t} = useTranslation()
  const params = useParams()
  const team = params.team || ''
  const tab = toTeamTab(params.tab)
  const cluster = useSelectedTeamTarget()
  const {data} = useTeamsQuery(cluster)
  const missing = !!data && !data.teams.some(row => row.name === team)
  const base = `/teams/${encodeURIComponent(team)}`
  return (
    <Layout
      contentType="default"
      breadcrumbs={
        <TeamBreadcrumbs
          titleKey="global.menu.teams"
          href="/teams"
          extra={{text: team, href: base}}
        />
      }
      notifications={
        <PageNotifications>
          {missing && (
            <Alert type="warning">
              {t('teamQuota.detail.notFound', {team})}
            </Alert>
          )}
        </PageNotifications>
      }
    >
      <ContentLayout
        header={
          <Header variant="h1" description={t('teamQuota.detail.description')}>
            {t('teamQuota.detail.title', {team})}
          </Header>
        }
      >
        <SpaceBetween size="l">
          <ClusterSelect />
          <TeamDetailContent
            cluster={cluster}
            team={team}
            tab={tab}
            basePath={base}
          />
        </SpaceBetween>
      </ContentLayout>
    </Layout>
  )
}

/** Leads (and admins on 我的团队): the team page of the team chosen in the team select. */
export function LeadTeamPage() {
  const {t} = useTranslation()
  const params = useParams()
  const tab = toTeamTab(params.tab)
  const cluster = useSelectedTeamTarget()
  const {target} = useLeadTarget()
  return (
    <Layout
      contentType="default"
      breadcrumbs={
        <TeamBreadcrumbs titleKey="global.menu.myTeam" href="/my-team" />
      }
      notifications={<PageNotifications />}
    >
      <ContentLayout
        header={
          <Header variant="h1" description={t('teamQuota.detail.description')}>
            {target?.team
              ? t('teamQuota.detail.title', {team: target.team})
              : t('global.menu.myTeam')}
          </Header>
        }
      >
        <SpaceBetween size="l">
          <LeadTeamSelect />
          {target?.team && (
            <TeamDetailContent
              cluster={cluster}
              team={target.team}
              tab={tab}
              basePath="/my-team"
            />
          )}
        </SpaceBetween>
      </ContentLayout>
    </Layout>
  )
}

function AdminTeamTabRedirect({tab}: {tab: TeamTab}) {
  const {target} = useLeadTarget()
  if (target?.team)
    return (
      <Navigate
        replace
        to={`/teams/${encodeURIComponent(target.team)}/${tab}`}
      />
    )
  // The selects choose the cluster (and so the teams); the redirect follows as soon as a team is known.
  return (
    <Layout contentType="default" notifications={<PageNotifications />}>
      <LeadTeamSelect />
    </Layout>
  )
}

/** Old pages (/team-jobs, /quota-holders) open the matching tab of the team page. */
export function TeamTabRedirect({tab}: {tab: TeamTab}) {
  const identity: UserIdentity | null = useState(['identity'])
  if (!isAdmin(identity)) return <Navigate replace to={`/my-team/${tab}`} />
  return <AdminTeamTabRedirect tab={tab} />
}
