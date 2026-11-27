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
  Container,
  ContentLayout,
  Header,
  SpaceBetween,
  Table,
  TableProps,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import Layout from '../old-pages/Layout'
import {errorMessage} from './api'
import {CapacityStatusBadge, nodeStateText} from './CapacityStatus'
import ClusterSelect, {useSelectedTeamTarget} from './ClusterSelect'
import {formatSpec} from './format'
import PageNotifications from './PageNotifications'
import {useCapacityQuery} from './queries'
import {TeamBreadcrumbs} from './TeamsPage'
import {MultiTypeQueuesAlert} from './TypeLimitsTable'
import {CapacityNodeRow, CapacityResponse, CapacityType} from './types'

interface NodeRow extends CapacityNodeRow {
  key: string
  partition: string
  instance_type: string | null
}

function nodeRows(data: CapacityResponse | undefined): NodeRow[] {
  if (!data) return []
  const managed = data.types.flatMap(type =>
    type.nodes.map((row, index) => ({
      ...row,
      key: `${type.partition}/${index}`,
      partition: type.partition,
      instance_type: type.instance_type,
    })),
  )
  const others = (data.partitions || []).flatMap(queue =>
    queue.nodes.map((row, index) => ({
      ...row,
      key: `${queue.partition}/${index}`,
      partition: queue.partition,
      instance_type: null,
    })),
  )
  return [...managed, ...others]
}

/** 机型与节点 (admin): capacity status and quota of every team per instance type, and the node states like sinfo. */
export default function CapacityPage() {
  const {t} = useTranslation()
  const cluster = useSelectedTeamTarget()
  const {data, isLoading, isError, error, refetch, isFetching} =
    useCapacityQuery(cluster)
  const teams = data?.teams || []
  const teamColumns: TableProps.ColumnDefinition<CapacityType>[] = teams.map(
    team => ({
      id: `team-${team}`,
      header: team,
      cell: type => {
        const row = type.teams.find(entry => entry.team === team)
        if (!row || (!row.limit && !row.running && !row.pending))
          return t('teamQuota.values.notAllowed')
        return t(
          row.pending || row.borrowed
            ? 'teamQuota.capacity.teamCellExtra'
            : 'teamQuota.capacity.teamCell',
          row,
        )
      },
    }),
  )
  return (
    <Layout
      contentType="default"
      breadcrumbs={
        <TeamBreadcrumbs titleKey="global.menu.capacity" href="/capacity" />
      }
      notifications={
        <PageNotifications>
          {isError && (
            <Alert type="error" header={t('teamQuota.capacity.loadError')}>
              {errorMessage(error)}
            </Alert>
          )}
          <MultiTypeQueuesAlert queues={data?.unmanaged_queues} />
        </PageNotifications>
      }
    >
      <ContentLayout
        header={
          <Header
            variant="h1"
            description={t('teamQuota.capacity.description')}
            actions={
              <Button
                iconName="refresh"
                loading={isFetching}
                onClick={() => refetch()}
              >
                {t('teamQuota.actions.refresh')}
              </Button>
            }
          >
            {t('teamQuota.capacity.title')}
          </Header>
        }
      >
        <SpaceBetween size="l">
          <Container>
            <ClusterSelect />
          </Container>
          <Table<CapacityType>
            variant="container"
            trackBy="instance_type"
            loading={!!cluster && isLoading}
            loadingText={t('teamQuota.capacity.loading')}
            items={data?.types || []}
            empty={
              <Box textAlign="center">{t('teamQuota.capacity.empty')}</Box>
            }
            header={
              <Header
                variant="h2"
                description={t('teamQuota.capacity.quotaDescription')}
              >
                {t('teamQuota.capacity.quotaTitle')}
              </Header>
            }
            columnDefinitions={[
              {
                id: 'type',
                header: t('teamQuota.capacity.columns.instanceType'),
                cell: type => type.instance_type,
              },
              {
                id: 'spec',
                header: t('teamQuota.capacity.columns.spec'),
                cell: type => formatSpec(type),
              },
              {
                id: 'status',
                header: t('teamQuota.capacity.columns.status'),
                cell: type => <CapacityStatusBadge status={type.status} />,
              },
              {
                id: 'nodes',
                header: t('teamQuota.capacity.columns.nodeTotal'),
                cell: type => type.node_total,
              },
              ...teamColumns,
            ]}
          />
          <Table<NodeRow>
            variant="container"
            trackBy="key"
            loading={!!cluster && isLoading}
            loadingText={t('teamQuota.capacity.loading')}
            items={nodeRows(data)}
            empty={
              <Box textAlign="center">{t('teamQuota.capacity.nodesEmpty')}</Box>
            }
            header={
              <Header
                variant="h2"
                description={t('teamQuota.capacity.nodesDescription')}
              >
                {t('teamQuota.capacity.nodesTitle')}
              </Header>
            }
            columnDefinitions={[
              {
                id: 'queue',
                header: t('teamQuota.capacity.columns.queue'),
                cell: row => row.partition,
              },
              {
                id: 'type',
                header: t('teamQuota.capacity.columns.instanceType'),
                cell: row =>
                  row.instance_type || t('teamQuota.capacity.otherQueue'),
              },
              {
                id: 'state',
                header: t('teamQuota.capacity.columns.state'),
                cell: row => nodeStateText(t, row.state),
              },
              {
                id: 'count',
                header: t('teamQuota.capacity.columns.count'),
                cell: row => row.count,
              },
              {
                id: 'nodes',
                header: t('teamQuota.capacity.columns.nodes'),
                cell: row => row.nodes || '-',
              },
              {
                id: 'reason',
                header: t('teamQuota.capacity.columns.reason'),
                cell: row => row.reason || '-',
              },
            ]}
          />
        </SpaceBetween>
      </ContentLayout>
    </Layout>
  )
}
