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
  ColumnLayout,
  Container,
  Header,
  SegmentedControl,
  SpaceBetween,
  Tabs,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {useNavigate, useParams} from 'react-router-dom'
import {UserIdentity} from '../../auth/types'
import {useState} from '../../store'
import {errorMessage} from '../api'
import {formatDollars, formatNumber} from '../format'
import {computePeriod, PeriodKind} from '../period'
import {isAdmin, teamsFromRoles} from '../roles'
import {
  LeadTeamPage,
  QuotaOverviewRow,
  QuotaOverviewTable,
  toTeamTab,
} from '../TeamDetail'
import MemberLayout, {MemberTeamSelect} from './MemberLayout'
import {
  useMemberOptionsQuery,
  useMemberTarget,
  useMemberTeamQuery,
} from './queries'
import {MemberTarget} from './types'
function Metric({label, value}: {label: string; value: React.ReactNode}) {
  return (
    <div>
      <Box variant="awsui-key-label">{label}</Box>
      <Box fontSize="heading-m">{value}</Box>
    </div>
  )
}
const MEMBER_TABS = ['overview', 'cost'] as const
/** 概览 of a member: the team's instance types with specs, quota, use and capacity status. */
function MemberOverview({target}: {target: MemberTarget | null}) {
  const {t} = useTranslation()
  const {data, isLoading, isError, error} = useMemberOptionsQuery(target)
  const rows = (data?.types || []).map<QuotaOverviewRow>(entry => ({
    entry,
    limit: entry.limit,
    running: entry.running,
    remaining: entry.remaining,
    status: entry.status,
  }))
  return (
    <SpaceBetween size="m">
      {isError && (
        <Alert type="error" header={t('teamQuota.member.team.loadError')}>
          {errorMessage(error)}
        </Alert>
      )}
      <QuotaOverviewTable rows={rows} loading={!!target && isLoading} />
    </SpaceBetween>
  )
}
/** 费用 of a member: own and team usage and cost of a period. */
function MemberCost({target}: {target: MemberTarget | null}) {
  const {t} = useTranslation()
  const [kind, setKind] = React.useState<PeriodKind>('thisMonth')
  const period = React.useMemo(() => computePeriod(kind)!, [kind])
  const {data, isLoading, isError, error} = useMemberTeamQuery(
    target,
    period.start,
    period.end,
  )
  const usage = data?.usage
  const mine = data?.mine
  return (
    <SpaceBetween size="l">
      <SegmentedControl
        selectedId={kind}
        onChange={({detail}) => setKind(detail.selectedId as PeriodKind)}
        options={[
          {id: 'thisMonth', text: t('teamQuota.member.team.thisMonth')},
          {id: 'lastMonth', text: t('teamQuota.member.team.lastMonth')},
          {id: 'last30Days', text: t('teamQuota.member.team.last30Days')},
        ]}
      />
      {isLoading && <Box>{t('teamQuota.member.loading')}</Box>}
      {isError && (
        <Alert type="error" header={t('teamQuota.member.team.loadError')}>
          {errorMessage(error)}
        </Alert>
      )}
      {data && (
        <SpaceBetween size="l">
          <Container
            header={
              <Header
                variant="h2"
                description={t(
                  'teamQuota.member.team.usageDescription',
                  data.period,
                )}
              >
                {t('teamQuota.member.team.usage')}
              </Header>
            }
          >
            <ColumnLayout columns={2} variant="text-grid">
              <SpaceBetween size="s">
                <Box variant="h3">
                  {t('teamQuota.member.team.mine', {user: mine?.user})}
                </Box>
                <Metric
                  label={t('teamQuota.member.team.instanceHours')}
                  value={formatNumber(mine?.instance_hours)}
                />
                <Metric
                  label={t('teamQuota.member.team.gpuHours')}
                  value={formatNumber(mine?.gpu_hours)}
                />
                <Metric
                  label={t('teamQuota.member.team.estimate')}
                  value={formatDollars(mine?.estimate_cost)}
                />
              </SpaceBetween>
              <SpaceBetween size="s">
                <Box variant="h3">{t('teamQuota.member.team.wholeTeam')}</Box>
                <Metric
                  label={t('teamQuota.member.team.instanceHours')}
                  value={formatNumber(usage?.instance_hours)}
                />
                <Metric
                  label={t('teamQuota.member.team.gpuHours')}
                  value={formatNumber(usage?.gpu_hours)}
                />
                <Metric
                  label={t('teamQuota.member.team.estimate')}
                  value={formatDollars(usage?.estimate_cost)}
                />
                <Metric
                  label={t('teamQuota.member.team.billed')}
                  value={
                    data.billed_available
                      ? formatDollars(
                          (usage?.compute_cost ?? 0) +
                            (usage?.shared_cost ?? 0),
                        )
                      : t('teamQuota.member.team.billedUnavailable')
                  }
                />
              </SpaceBetween>
            </ColumnLayout>
          </Container>
          <Box variant="small">{t('teamQuota.member.team.note')}</Box>
        </SpaceBetween>
      )}
    </SpaceBetween>
  )
}
function MemberTeamPage() {
  const {t} = useTranslation()
  const navigate = useNavigate()
  const params = useParams()
  const tab = toTeamTab(params.tab, MEMBER_TABS)
  const {target} = useMemberTarget()
  return (
    <MemberLayout page="myTeam">
      <MemberTeamSelect />
      <Tabs
        activeTabId={tab}
        onChange={({detail}) =>
          navigate(
            detail.activeTabId === 'overview'
              ? '/my-team'
              : `/my-team/${detail.activeTabId}`,
          )
        }
        tabs={MEMBER_TABS.map(id => ({
          id,
          label: t(`teamQuota.detail.tabs.${id}`),
          content:
            id !== tab ? null : id === 'overview' ? (
              <MemberOverview target={target} />
            ) : (
              <MemberCost target={target} />
            ),
        }))}
      />
    </MemberLayout>
  )
}
/** 我的团队 adapts to the role: leads and admins get the whole team page, members 概览 and 费用. */
export default function MyTeamPage() {
  const identity: UserIdentity | null = useState(['identity'])
  if (isAdmin(identity) || teamsFromRoles(identity).length > 0)
    return <LeadTeamPage />
  return <MemberTeamPage />
}
