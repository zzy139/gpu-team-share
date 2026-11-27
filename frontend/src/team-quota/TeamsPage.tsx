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
  Popover,
  SpaceBetween,
  Table,
  TableProps,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {useNavigate} from 'react-router-dom'
import {UserIdentity} from '../auth/types'
import {DeleteDialog, hideDialog, showDialog} from '../components/DeleteDialog'
import EmptyState from '../components/EmptyState'
import {notify} from '../model'
import {BreadcrumbGroupNavigate} from '../old-pages/Configure/Components'
import Layout from '../old-pages/Layout'
import {useState} from '../store'
import {errorMessage} from './api'
import ClusterSelect, {useSelectedTeamTarget} from './ClusterSelect'
import CreateTeamModal from './CreateTeamModal'
import EditTeamModal from './EditTeamModal'
import {formatDollars, formatPercent, formatSpec, machinesInUse} from './format'
import MembersModal from './MembersModal'
import {
  useDeleteTeamMutation,
  useLifecycleTeamsQuery,
  useTeamsQuery,
} from './queries'
import {isAdmin} from './roles'
import PageNotifications from './PageNotifications'
import TeamProvisionModal from './TeamProvisionModal'
import {MultiTypeQueuesAlert} from './TypeLimitsTable'
import {RetainedResources, Team} from './types'

const DELETE_DIALOG_ID = 'deleteTeam'
type ModalKind = 'create' | 'edit' | 'members' | 'provision' | null

export function TeamBreadcrumbs({
  titleKey,
  href,
  extra,
}: {
  titleKey:
    | 'global.menu.teams'
    | 'global.menu.teamUsage'
    | 'global.menu.capacity'
    | 'global.menu.myTeam'
    | 'global.menu.audit'
  href: string
  extra?: {text: string; href: string}
}) {
  const {t} = useTranslation()
  return (
    <BreadcrumbGroupNavigate
      items={[
        {text: t('global.menu.header'), href: '/'},
        {text: t(titleKey), href},
        ...(extra ? [extra] : []),
      ]}
    />
  )
}

/** Fairshare column header with an info popover (what fairshare does and does not do). */
export function FairshareHeader() {
  const {t} = useTranslation()
  return (
    <SpaceBetween direction="horizontal" size="xxs">
      <span>{t('teamQuota.columns.fairshare')}</span>
      <Popover
        triggerType="custom"
        size="medium"
        content={t('teamQuota.columns.fairshareInfo')}
      >
        <Button
          variant="inline-icon"
          iconName="status-info"
          ariaLabel={t('teamQuota.columns.fairshareInfoAria')}
        />
      </Popover>
    </SpaceBetween>
  )
}

const RETAINED_KEYS: (keyof RetainedResources)[] = [
  'ad_group',
  'ad_users',
  'team_path',
  'ecr_repo',
  'secrets',
  'cognito_users',
]

export function RetainedList({retained}: {retained: RetainedResources}) {
  const {t} = useTranslation()
  return (
    <ul>
      {RETAINED_KEYS.filter(key => {
        const value = retained[key]
        return Array.isArray(value) ? value.length > 0 : !!value
      }).map(key => {
        const value = retained[key]
        return (
          <li key={key}>
            {t(`teamQuota.delete.retained.${key}`)}:{' '}
            {Array.isArray(value) ? value.join(', ') : value}
          </li>
        )
      })}
    </ul>
  )
}

export default function TeamsPage() {
  const {t} = useTranslation()
  const navigate = useNavigate()
  const identity: UserIdentity | null = useState(['identity'])
  const target = useSelectedTeamTarget()
  const {data, isLoading, isError, error, refetch, isFetching} =
    useTeamsQuery(target)
  const deleteMutation = useDeleteTeamMutation(target)
  const admin = isAdmin(identity)
  const editable = admin && !!data?.editable
  // Admin only: team leads never call the lifecycle endpoints.
  const lifecycleQuery = useLifecycleTeamsQuery(target, admin)
  const lifecycle = lifecycleQuery.data
  const records = React.useMemo(() => lifecycle?.records || [], [lifecycle])
  const provisioning = records.filter(r => r.state === 'provisioning')
  const [provisionTeam, setProvisionTeam] = React.useState<string | null>(null)
  const [retained, setRetained] = React.useState<{
    team: string
    retained: RetainedResources
  } | null>(null)
  const [selectedName, setSelectedName] = React.useState<string | null>(null)
  const [modal, setModal] = React.useState<ModalKind>(null)

  const teams = React.useMemo(() => data?.teams || [], [data])
  const selected = teams.find(team => team.name === selectedName) || null
  const catalog = React.useMemo(() => data?.catalog || [], [data])
  const unmanagedQueues = data?.unmanaged_queues
  const managedSelected = records.some(
    r => r.team === selected?.name && r.state === 'active',
  )

  const deleteTeam = () => {
    hideDialog(DELETE_DIALOG_ID)
    if (!selected) return
    const name = selected.name
    deleteMutation.mutate(name, {
      onSuccess: response => {
        notify(t('teamQuota.notifications.deleted', {team: name}), 'success')
        setSelectedName(null)
        if (response?.retained && Object.keys(response.retained).length)
          setRetained({team: name, retained: response.retained})
      },
      onError: err => notify(errorMessage(err), 'error'),
    })
  }

  const typeColumns: TableProps.ColumnDefinition<Team>[] = catalog.map(
    entry => ({
      id: `type-${entry.instance_type}`,
      header: (
        <span>
          {entry.instance_type}
          <Box variant="small" display="block">
            {formatSpec(entry)}
          </Box>
        </span>
      ),
      cell: (team: Team) => {
        const limit = team.type_limits?.[entry.instance_type] || 0
        const used = machinesInUse(team, entry.instance_type)
        if (!limit && !used) return t('teamQuota.values.notAllowed')
        return t('teamQuota.values.machines', {used, limit})
      },
    }),
  )

  const columns: TableProps.ColumnDefinition<Team>[] = [
    {
      id: 'name',
      header: t('teamQuota.columns.name'),
      cell: (team: Team) => (
        <Link
          href={`/teams/${encodeURIComponent(team.name)}`}
          onFollow={event => {
            event.preventDefault()
            navigate(`/teams/${encodeURIComponent(team.name)}`)
          }}
        >
          {team.name}
        </Link>
      ),
    },
    {
      id: 'fairshare',
      header: <FairshareHeader />,
      cell: (team: Team) =>
        team.fairshare === null
          ? '-'
          : `${team.fairshare} (${formatPercent(team.fairshare_share)})`,
    },
    ...typeColumns,
    {
      id: 'running',
      header: t('teamQuota.columns.running'),
      cell: (team: Team) =>
        t('teamQuota.values.running', {
          machines: team.running.machines,
          gpus: team.running.gpus,
          jobs: team.running.jobs,
        }),
    },
    {
      id: 'budget',
      header: t('teamQuota.columns.budget'),
      cell: (team: Team) =>
        team.budget_usd ? formatDollars(team.budget_usd) : '-',
    },
    {
      id: 'envCap',
      header: t('teamQuota.columns.envCap'),
      cell: (team: Team) =>
        team.env_storage_cap_gb
          ? t('teamQuota.values.gb', {gb: team.env_storage_cap_gb})
          : '-',
    },
    {
      id: 'members',
      header: t('teamQuota.columns.members'),
      cell: (team: Team) => team.members.join(', ') || '-',
    },
  ]

  const actions = editable ? (
    <SpaceBetween direction="horizontal" size="xs">
      <Button
        iconName="refresh"
        loading={isFetching}
        onClick={() => refetch()}
        ariaLabel={t('teamQuota.actions.refresh')}
      >
        {t('teamQuota.actions.refresh')}
      </Button>
      <Button disabled={!selected} onClick={() => setModal('members')}>
        {t('teamQuota.actions.manageMembers')}
      </Button>
      <Button disabled={!selected} onClick={() => setModal('edit')}>
        {t('teamQuota.actions.editLimits')}
      </Button>
      <Button disabled={!selected} onClick={() => showDialog(DELETE_DIALOG_ID)}>
        {t('teamQuota.actions.delete')}
      </Button>
      <Button variant="primary" onClick={() => setModal('create')}>
        {t('teamQuota.actions.createTeam')}
      </Button>
    </SpaceBetween>
  ) : undefined

  return (
    <Layout
      contentType="table"
      breadcrumbs={
        <TeamBreadcrumbs titleKey="global.menu.teams" href="/teams" />
      }
      notifications={
        <PageNotifications>
          {!editable && <Alert type="info">{t('teamQuota.readOnly')}</Alert>}
          <MultiTypeQueuesAlert queues={unmanagedQueues} />
          {editable &&
            provisioning.map(record => (
              <Alert
                key={record.team}
                type="warning"
                header={t('teamQuota.lifecycle.provisioningHeader', {
                  team: record.team,
                })}
                action={
                  <Button
                    onClick={() => {
                      setProvisionTeam(record.team)
                      setModal('provision')
                    }}
                  >
                    {t('teamQuota.lifecycle.resume')}
                  </Button>
                }
              >
                {t('teamQuota.lifecycle.provisioningBody')}
              </Alert>
            ))}
          {retained && (
            <Alert
              type="info"
              dismissible
              onDismiss={() => setRetained(null)}
              header={t('teamQuota.delete.retainedHeader', {
                team: retained.team,
              })}
            >
              <RetainedList retained={retained.retained} />
            </Alert>
          )}
          {isError && (
            <Alert type="error" header={t('teamQuota.loadError')}>
              {errorMessage(error)}
            </Alert>
          )}
        </PageNotifications>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        resizableColumns
        trackBy="name"
        loading={!!target && isLoading}
        loadingText={t('teamQuota.loading')}
        items={teams}
        columnDefinitions={columns}
        selectionType={editable ? 'single' : undefined}
        selectedItems={selected ? [selected] : []}
        onSelectionChange={({detail}) =>
          setSelectedName(detail.selectedItems[0]?.name ?? null)
        }
        filter={<ClusterSelect />}
        empty={
          <EmptyState
            title={t('teamQuota.empty.title')}
            subtitle={t('teamQuota.empty.subtitle')}
          />
        }
        header={
          <Header
            variant="awsui-h1-sticky"
            counter={`(${teams.length})`}
            description={t('teamQuota.header.description')}
            actions={actions}
          >
            {t('teamQuota.header.title')}
          </Header>
        }
      />
      {editable && (
        <>
          <CreateTeamModal
            target={target}
            catalog={catalog}
            teams={teams}
            unmanagedQueues={unmanagedQueues}
            visible={modal === 'create'}
            onDismiss={() => setModal(null)}
            lifecycle={lifecycle}
            onProvision={team => {
              setProvisionTeam(team)
              setModal('provision')
            }}
          />
          <TeamProvisionModal
            target={target}
            team={provisionTeam}
            visible={modal === 'provision'}
            onDismiss={() => setModal(null)}
          />
          <EditTeamModal
            team={selected}
            target={target}
            catalog={catalog}
            teams={teams}
            unmanagedQueues={unmanagedQueues}
            managed={managedSelected}
            visible={modal === 'edit'}
            onDismiss={() => setModal(null)}
          />
          <MembersModal
            team={selected}
            target={target}
            visible={modal === 'members'}
            onDismiss={() => setModal(null)}
            managed={managedSelected}
            emailDomains={lifecycle?.email_domains || []}
          />
          <DeleteDialog
            id={DELETE_DIALOG_ID}
            header={t('teamQuota.delete.title')}
            deleteCallback={deleteTeam}
          >
            <SpaceBetween size="xs">
              <Box>{t('teamQuota.delete.body', {team: selected?.name})}</Box>
              <Box>{t('teamQuota.delete.kept')}</Box>
            </SpaceBetween>
          </DeleteDialog>
        </>
      )}
    </Layout>
  )
}
