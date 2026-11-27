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
  SpaceBetween,
  Table,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {errorMessage} from '../api'
import {stateLabel} from '../member/format'
import {MemberTarget} from '../member/types'
import {useLeadJobsQuery} from '../queries'
import {LeadJob} from '../types'

/** Job metadata of the team (no scripts, output or files): the 作业 tab of the team page. */
export function TeamJobsContent({target}: {target: MemberTarget | null}) {
  const {t} = useTranslation()
  const {data, isLoading, isError, error, refetch, isFetching} =
    useLeadJobsQuery(target, target?.team || '')
  return (
    <SpaceBetween size="m">
      {isError && (
        <Alert type="error" header={t('teamQuota.lead.jobs.loadError')}>
          {errorMessage(error)}
        </Alert>
      )}
      <Table<LeadJob>
        variant="container"
        trackBy="job_id"
        loading={!!target && isLoading}
        loadingText={t('teamQuota.member.loading')}
        items={data?.jobs || []}
        empty={<Box textAlign="center">{t('teamQuota.lead.jobs.empty')}</Box>}
        header={
          <Header
            counter={`(${data?.jobs.length ?? 0})`}
            description={t('teamQuota.lead.jobs.description')}
            actions={
              <Button
                iconName="refresh"
                loading={isFetching}
                onClick={() => refetch()}
              >
                {t('teamQuota.member.refresh')}
              </Button>
            }
          >
            {t('teamQuota.lead.jobs.title', {team: data?.team || '-'})}
          </Header>
        }
        columnDefinitions={[
          {
            id: 'id',
            header: t('teamQuota.member.columns.jobId'),
            cell: j => j.job_id,
          },
          {
            id: 'user',
            header: t('teamQuota.lead.columns.user'),
            cell: j => j.user,
          },
          {
            id: 'name',
            header: t('teamQuota.member.columns.name'),
            cell: j => j.name,
          },
          {
            id: 'type',
            header: t('teamQuota.member.columns.instanceType'),
            cell: j => j.instance_type || j.partition,
          },
          {
            id: 'nodes',
            header: t('teamQuota.member.submit.nodes'),
            cell: j => j.nodes ?? '-',
          },
          {
            id: 'state',
            header: t('teamQuota.member.columns.state'),
            cell: j => stateLabel(t, j.state),
          },
          {
            id: 'reason',
            header: t('teamQuota.member.columns.reason'),
            cell: j => j.reason_text || '-',
          },
          {
            id: 'qos',
            header: t('teamQuota.member.columns.qos'),
            cell: j => j.qos || '-',
          },
          {
            id: 'elapsed',
            header: t('teamQuota.member.columns.elapsed'),
            cell: j => j.elapsed || '-',
          },
          {
            id: 'limit',
            header: t('teamQuota.member.columns.timeLimit'),
            cell: j => j.time_limit || '-',
          },
        ]}
      />
    </SpaceBetween>
  )
}
