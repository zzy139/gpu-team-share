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
  Header,
  Link,
  Modal,
  SegmentedControl,
  SpaceBetween,
  Table,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {useNavigate} from 'react-router-dom'
import {notify} from '../../model'
import {errorMessage} from '../api'
import {toIsoDate} from '../period'
import {stateLabel} from './format'
import {JobAlertBadges, LastUpdated, timeLimitText} from './JobAlerts'
import MemberLayout, {MemberTeamSelect} from './MemberLayout'
import {
  useCancelJobMutation,
  useMemberHistoryQuery,
  useMemberJobsQuery,
  useMemberTarget,
} from './queries'
import {MemberHistoryJob, MemberJob} from './types'

export type HistoryRange = '1' | '7' | '31'

export function historyPeriod(days: HistoryRange, today: Date = new Date()) {
  const end = new Date(
    today.getFullYear(),
    today.getMonth(),
    today.getDate() + 1,
  )
  const start = new Date(
    end.getFullYear(),
    end.getMonth(),
    end.getDate() - Number(days),
  )
  return {start: toIsoDate(start), end: toIsoDate(end)}
}

function JobLink({jobId}: {jobId: string}) {
  const navigate = useNavigate()
  const href = `/my-jobs/${encodeURIComponent(jobId)}`
  return (
    <Link
      href={href}
      onFollow={event => {
        event.preventDefault()
        navigate(href)
      }}
    >
      {jobId}
    </Link>
  )
}

export default function MyJobsPage() {
  const {t} = useTranslation()
  const navigate = useNavigate()
  const {target} = useMemberTarget()
  const jobs = useMemberJobsQuery(target)
  const [range, setRange] = React.useState<HistoryRange>('7')
  const period = React.useMemo(() => historyPeriod(range), [range])
  const history = useMemberHistoryQuery(target, period.start, period.end)
  const cancelMutation = useCancelJobMutation(target)
  const [toCancel, setToCancel] = React.useState<MemberJob | null>(null)

  const cancel = () => {
    if (!toCancel) return
    const jobId = toCancel.job_id
    setToCancel(null)
    cancelMutation.mutate(jobId, {
      onSuccess: () =>
        notify(t('teamQuota.member.jobs.cancelled', {jobId}), 'success'),
      onError: err => notify(errorMessage(err), 'error'),
    })
  }

  return (
    <MemberLayout page="myJobs">
      <MemberTeamSelect />
      {jobs.isError && (
        <Alert type="error" header={t('teamQuota.member.jobs.loadError')}>
          {errorMessage(jobs.error)}
        </Alert>
      )}
      <Table
        variant="container"
        trackBy="job_id"
        loading={!!target && jobs.isLoading}
        loadingText={t('teamQuota.member.loading')}
        items={jobs.data?.jobs || []}
        empty={<Box textAlign="center">{t('teamQuota.member.jobs.empty')}</Box>}
        header={
          <Header
            counter={`(${jobs.data?.jobs.length ?? 0})`}
            description={
              <SpaceBetween direction="horizontal" size="xs">
                <span>{t('teamQuota.member.jobs.description')}</span>
                <LastUpdated updatedAt={jobs.dataUpdatedAt} />
              </SpaceBetween>
            }
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  iconName="refresh"
                  loading={jobs.isFetching}
                  onClick={() => jobs.refetch()}
                >
                  {t('teamQuota.member.refresh')}
                </Button>
                <Button variant="primary" onClick={() => navigate('/submit')}>
                  {t('global.menu.submitJob')}
                </Button>
              </SpaceBetween>
            }
          >
            {t('teamQuota.member.jobs.title')}
          </Header>
        }
        columnDefinitions={[
          {
            id: 'id',
            header: t('teamQuota.member.columns.jobId'),
            cell: j => <JobLink jobId={j.job_id} />,
          },
          {
            id: 'name',
            header: t('teamQuota.member.columns.name'),
            cell: j => j.name,
          },
          {
            id: 'instanceType',
            header: t('teamQuota.member.columns.instanceType'),
            cell: j => j.instance_type || j.partition,
          },
          {
            id: 'qos',
            header: t('teamQuota.member.columns.qos'),
            cell: j => j.qos || '-',
          },
          {
            id: 'state',
            header: t('teamQuota.member.columns.state'),
            cell: j => stateLabel(t, j.state),
          },
          {
            id: 'alerts',
            header: t('teamQuota.member.columns.alerts'),
            cell: j =>
              j.alerts?.length ? <JobAlertBadges alerts={j.alerts} /> : '-',
          },
          {
            id: 'reason',
            header: t('teamQuota.member.columns.reason'),
            cell: j =>
              j.reason_text || (j.state === 'PENDING' ? j.reason || '-' : '-'),
          },
          {
            id: 'gres',
            header: t('teamQuota.member.columns.gres'),
            cell: j => j.gres || '-',
          },
          {
            id: 'elapsed',
            header: t('teamQuota.member.columns.elapsed'),
            cell: j => j.elapsed || '-',
          },
          {
            id: 'limit',
            header: t('teamQuota.member.columns.timeLimit'),
            cell: j => timeLimitText(t, j.time_limit),
          },
          {
            id: 'actions',
            header: '',
            cell: j => (
              <Button
                variant="link"
                ariaLabel={t('teamQuota.member.jobs.cancelAria', {
                  jobId: j.job_id,
                })}
                disabled={cancelMutation.isLoading}
                onClick={() => setToCancel(j)}
              >
                {t('teamQuota.member.jobs.cancel')}
              </Button>
            ),
          },
        ]}
      />
      <Table<MemberHistoryJob>
        variant="container"
        trackBy="job_id"
        loading={!!target && history.isLoading}
        loadingText={t('teamQuota.member.loading')}
        items={history.data?.jobs || []}
        empty={
          <Box textAlign="center">{t('teamQuota.member.history.empty')}</Box>
        }
        header={
          <Header
            actions={
              <SegmentedControl
                selectedId={range}
                onChange={({detail}) =>
                  setRange(detail.selectedId as HistoryRange)
                }
                options={[
                  {id: '1', text: t('teamQuota.member.history.day')},
                  {id: '7', text: t('teamQuota.member.history.week')},
                  {id: '31', text: t('teamQuota.member.history.month')},
                ]}
              />
            }
          >
            {t('teamQuota.member.history.title')}
          </Header>
        }
        columnDefinitions={[
          {
            id: 'id',
            header: t('teamQuota.member.columns.jobId'),
            cell: j => <JobLink jobId={j.job_id} />,
          },
          {
            id: 'name',
            header: t('teamQuota.member.columns.name'),
            cell: j => j.name,
          },
          {
            id: 'instanceType',
            header: t('teamQuota.member.columns.instanceType'),
            cell: j => j.instance_type || j.partition,
          },
          {
            id: 'state',
            header: t('teamQuota.member.columns.state'),
            cell: j => stateLabel(t, j.state),
          },
          {
            id: 'start',
            header: t('teamQuota.member.columns.start'),
            cell: j => j.start || '-',
          },
          {
            id: 'end',
            header: t('teamQuota.member.columns.end'),
            cell: j => j.end || '-',
          },
          {
            id: 'exit',
            header: t('teamQuota.member.columns.exitCode'),
            cell: j => j.exit_code || '-',
          },
          {
            id: 'resubmit',
            header: '',
            cell: j => (
              <Button
                variant="link"
                ariaLabel={t('teamQuota.member.resubmit.aria', {
                  jobId: j.job_id,
                })}
                onClick={() =>
                  navigate(`/submit?resubmit=${encodeURIComponent(j.job_id)}`)
                }
              >
                {t('teamQuota.member.resubmit.button')}
              </Button>
            ),
          },
        ]}
      />
      <Modal
        visible={!!toCancel}
        onDismiss={() => setToCancel(null)}
        header={t('teamQuota.member.jobs.confirmTitle')}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setToCancel(null)}>
                {t('teamQuota.actions.cancel')}
              </Button>
              <Button variant="primary" onClick={cancel}>
                {t('teamQuota.member.jobs.confirmCancel')}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {t('teamQuota.member.jobs.confirmBody', {
          jobId: toCancel?.job_id,
          name: toCancel?.name,
        })}
      </Modal>
    </MemberLayout>
  )
}
