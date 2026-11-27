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
  ColumnLayout,
  Container,
  Header,
  Link,
  SpaceBetween,
  Table,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {useNavigate, useParams} from 'react-router-dom'
import {errorMessage} from '../api'
import {ListMemberDir} from './api'
import {stateLabel} from './format'
import {
  isUnlimited,
  JobAlertBadges,
  LastUpdated,
  timeLimitText,
} from './JobAlerts'
import LogViewer from './LogViewer'
import MemberLayout from './MemberLayout'
import {isFinalState, useMemberJobQuery, useMemberTarget} from './queries'
import {
  MemberDirEntry,
  MemberDirListing,
  MemberJobDetail,
  MemberTarget,
} from './types'

function Field({label, value}: {label: string; value: React.ReactNode}) {
  return (
    <div>
      <Box variant="awsui-key-label">{label}</Box>
      <div>{value ?? '-'}</div>
    </div>
  )
}

function parentOf(path: string) {
  const index = path.replace(/\/+$/, '').lastIndexOf('/')
  return index <= 0 ? '/' : path.slice(0, index)
}

/** Browse a directory (limited by the server to the team directory and the home directory). */
export function FileBrowser({
  target,
  root,
  onOpen,
}: {
  target: MemberTarget | null
  root: string
  onOpen: (path: string) => void
}) {
  const {t} = useTranslation()
  const [path, setPath] = React.useState(root)
  const [listing, setListing] = React.useState<MemberDirListing | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => setPath(root), [root])
  React.useEffect(() => {
    if (!target || !path) return
    setError(null)
    ListMemberDir(target, path)
      .then(setListing)
      .catch(err => {
        setListing(null)
        setError(errorMessage(err))
      })
  }, [target, path])

  const open = (entry: MemberDirEntry) => {
    const full = `${(listing?.path || path).replace(/\/+$/, '')}/${entry.name}`
    if (entry.type === 'dir') setPath(full)
    else onOpen(full)
  }

  return (
    <Table
      variant="container"
      trackBy="name"
      items={listing?.entries || []}
      empty={<Box textAlign="center">{t('teamQuota.member.files.empty')}</Box>}
      header={
        <Header
          variant="h2"
          description={listing?.path || path}
          actions={
            <Button
              disabled={path === root}
              onClick={() => setPath(parentOf(path))}
            >
              {t('teamQuota.member.files.up')}
            </Button>
          }
        >
          {t('teamQuota.member.files.title')}
        </Header>
      }
      footer={
        <>
          {error && <Alert type="error">{error}</Alert>}
          {listing?.truncated && (
            <Box variant="small">{t('teamQuota.member.files.truncated')}</Box>
          )}
        </>
      }
      columnDefinitions={[
        {
          id: 'name',
          header: t('teamQuota.member.files.name'),
          cell: entry => (
            <Link
              onFollow={event => {
                event.preventDefault()
                open(entry)
              }}
            >
              {entry.type === 'dir' ? `${entry.name}/` : entry.name}
            </Link>
          ),
        },
        {
          id: 'size',
          header: t('teamQuota.member.files.size'),
          cell: entry => (entry.type === 'dir' ? '-' : entry.size),
        },
        {
          id: 'mtime',
          header: t('teamQuota.member.files.mtime'),
          cell: entry =>
            entry.mtime ? new Date(entry.mtime * 1000).toLocaleString() : '-',
        },
      ]}
    />
  )
}

const PENDING_STATES = new Set(['PENDING', 'CONFIGURING'])

function stateWord(state: string | null | undefined) {
  return (state || '').split(' ')[0].toUpperCase()
}

/** Final: the end time is real. Otherwise '-', or 预计最晚结束 for a running job with a time limit. */
function endField(t: (key: string) => string, job: MemberJobDetail) {
  const final =
    job.end_is_projected === undefined
      ? isFinalState(job.state)
      : !job.end_is_projected
  if (final)
    return {label: t('teamQuota.member.columns.end'), value: job.end || '-'}
  if (
    stateWord(job.state) === 'RUNNING' &&
    !isUnlimited(job.time_limit) &&
    job.end
  )
    return {label: t('teamQuota.member.detail.latestEnd'), value: job.end}
  return {label: t('teamQuota.member.columns.end'), value: '-'}
}

function JobDetails({
  job,
  updatedAt,
}: {
  job: MemberJobDetail
  updatedAt: number
}) {
  const {t} = useTranslation()
  const navigate = useNavigate()
  const end = endField(t, job)
  const pending = PENDING_STATES.has(stateWord(job.state))
  const final = isFinalState(job.state)
  return (
    <Container
      header={
        <Header
          variant="h2"
          description={
            <SpaceBetween direction="horizontal" size="xs">
              <LastUpdated updatedAt={updatedAt} />
              <Box variant="small" color="text-body-secondary">
                {final
                  ? t('teamQuota.member.detail.final')
                  : t('teamQuota.member.detail.autoRefresh')}
              </Box>
            </SpaceBetween>
          }
          actions={
            <Button
              onClick={() =>
                navigate(`/submit?resubmit=${encodeURIComponent(job.job_id)}`)
              }
            >
              {t('teamQuota.member.resubmit.button')}
            </Button>
          }
        >
          {t('teamQuota.member.detail.title', {jobId: job.job_id})}
        </Header>
      }
    >
      <ColumnLayout columns={3} variant="text-grid">
        <Field label={t('teamQuota.member.columns.name')} value={job.name} />
        <Field
          label={t('teamQuota.member.columns.state')}
          value={
            <SpaceBetween direction="horizontal" size="xs">
              <span>{stateLabel(t, job.state)}</span>
              <JobAlertBadges alerts={job.alerts} />
            </SpaceBetween>
          }
        />
        <Field
          label={t('teamQuota.member.columns.reason')}
          value={job.reason_text || (pending && job.reason) || '-'}
        />
        <Field
          label={t('teamQuota.member.columns.partition')}
          value={job.partition}
        />
        <Field label={t('teamQuota.member.columns.qos')} value={job.qos} />
        <Field
          label={t('teamQuota.member.columns.account')}
          value={job.account}
        />
        <Field
          label={t('teamQuota.member.columns.submit')}
          value={job.submit}
        />
        <Field label={t('teamQuota.member.columns.start')} value={job.start} />
        <Field label={end.label} value={end.value} />
        <Field
          label={t('teamQuota.member.columns.timeLimit')}
          value={timeLimitText(t, job.time_limit)}
        />
        <Field
          label={t('teamQuota.member.columns.exitCode')}
          value={job.exit_code}
        />
        <Field label={t('teamQuota.member.columns.tres')} value={job.tres} />
        <Field
          label={t('teamQuota.member.detail.workdir')}
          value={job.workdir}
        />
        <Field
          label={t('teamQuota.member.detail.stdout')}
          value={job.stdout_path}
        />
        <Field
          label={t('teamQuota.member.detail.stderr')}
          value={job.stderr_path}
        />
      </ColumnLayout>
    </Container>
  )
}

/** Why the standard output cannot be shown, instead of a read error. Null when it can be read. */
function OutputNotice({job}: {job: MemberJobDetail}) {
  const {t} = useTranslation()
  switch (job.output_state) {
    case 'not_started':
      return (
        <Alert type="info" header={t('teamQuota.member.output.title')}>
          {t('teamQuota.member.output.notStarted')}
        </Alert>
      )
    case 'never_started':
      return (
        <Alert type="info" header={t('teamQuota.member.output.title')}>
          {t('teamQuota.member.output.neverStarted')}
        </Alert>
      )
    case 'env_deleted':
      return (
        <Alert type="info" header={t('teamQuota.member.output.title')}>
          {t('teamQuota.member.output.envDeleted', {
            id: job.env_deleted
              ? `${job.env_deleted.name}:${job.env_deleted.version}`
              : '-',
            by: job.env_deleted?.by || '-',
            at: job.env_deleted?.at || '-',
          })}
        </Alert>
      )
    case 'missing':
      return (
        <Alert type="error" header={t('teamQuota.member.output.missingTitle')}>
          <SpaceBetween size="xxs">
            <Box>
              {t('teamQuota.member.output.missing', {
                path: job.stdout_path || '-',
              })}
            </Box>
            <ul style={{margin: 0}}>
              <li>{t('teamQuota.member.output.reasonWorkdir')}</li>
              <li>{t('teamQuota.member.output.reasonPath')}</li>
              <li>{t('teamQuota.member.output.reasonRemoved')}</li>
            </ul>
          </SpaceBetween>
        </Alert>
      )
    default:
      return null
  }
}

export default function JobDetailPage() {
  const {t} = useTranslation()
  const {jobId = ''} = useParams()
  const {target} = useMemberTarget()
  const {
    data: job,
    isLoading,
    isError,
    error,
    dataUpdatedAt,
  } = useMemberJobQuery(target, jobId)
  const [opened, setOpened] = React.useState<string | null>(null)
  // older servers do not send output_state: read the files as before
  const readable = !job?.output_state || job.output_state === 'ok'
  const running = stateWord(job?.state) === 'RUNNING'

  return (
    <MemberLayout
      page="myJobs"
      extraCrumb={{text: jobId, href: `/my-jobs/${encodeURIComponent(jobId)}`}}
    >
      {isLoading && <Box>{t('teamQuota.member.loading')}</Box>}
      {isError && (
        <Alert type="error" header={t('teamQuota.member.detail.loadError')}>
          {errorMessage(error)}
        </Alert>
      )}
      {job && (
        <SpaceBetween size="l">
          <JobDetails job={job} updatedAt={dataUpdatedAt} />
          <OutputNotice job={job} />
          {readable && (
            <LogViewer
              target={target}
              path={job.stdout_path}
              title={t('teamQuota.member.logs.stdout')}
              follow={running}
              refreshKey={dataUpdatedAt}
            />
          )}
          {readable &&
            job.stderr_path &&
            job.stderr_path !== job.stdout_path && (
              <LogViewer
                target={target}
                path={job.stderr_path}
                title={t('teamQuota.member.logs.stderr')}
                follow={running}
                refreshKey={dataUpdatedAt}
              />
            )}
          <FileBrowser
            target={target}
            root={job.workdir || job.team_root}
            onOpen={setOpened}
          />
          {opened && (
            <LogViewer
              target={target}
              path={opened}
              title={t('teamQuota.member.files.viewer')}
            />
          )}
        </SpaceBetween>
      )}
    </MemberLayout>
  )
}
