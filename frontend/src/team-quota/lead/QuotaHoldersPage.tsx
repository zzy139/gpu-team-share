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
  Header,
  SpaceBetween,
  Table,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {errorMessage} from '../api'
import BudgetAlert from '../BudgetAlert'
import {MemberTarget} from '../member/types'
import {useLeadQuotaQuery} from '../queries'
import {MultiTypeQueuesAlert} from '../TypeLimitsTable'
import {LeadQuotaType} from '../types'

/** Per instance type: the team's machine count and which members hold the machines (team page, 成员 tab). */
export function QuotaHoldersContent({target}: {target: MemberTarget | null}) {
  const {t} = useTranslation()
  const {data, isLoading, isError, error} = useLeadQuotaQuery(
    target,
    target?.team || '',
  )
  return (
    <SpaceBetween size="m">
      {isError && (
        <Alert type="error" header={t('teamQuota.lead.quota.loadError')}>
          {errorMessage(error)}
        </Alert>
      )}
      <BudgetAlert banner={data?.budget} />
      <MultiTypeQueuesAlert queues={data?.unmanaged_queues} />
      <Table<LeadQuotaType>
        variant="container"
        trackBy="instance_type"
        loading={!!target && isLoading}
        loadingText={t('teamQuota.member.loading')}
        items={data?.types || []}
        empty={<Box textAlign="center">{t('teamQuota.typeLimits.empty')}</Box>}
        header={
          <Header description={t('teamQuota.lead.quota.description')}>
            {t('teamQuota.lead.quota.title', {team: data?.team || '-'})}
          </Header>
        }
        columnDefinitions={[
          {
            id: 'type',
            header: t('teamQuota.member.submit.instanceType'),
            cell: row => row.instance_type,
          },
          {
            id: 'gpus',
            header: t('teamQuota.typeLimits.gpus'),
            cell: row => row.gpus,
          },
          {
            id: 'limit',
            header: t('teamQuota.member.submit.limit'),
            cell: row =>
              row.limit ? row.limit : t('teamQuota.values.notAllowed'),
          },
          {
            id: 'running',
            header: t('teamQuota.member.submit.running'),
            cell: row => row.running,
          },
          {
            id: 'pending',
            header: t('teamQuota.member.team.pending'),
            cell: row => row.pending,
          },
          {
            id: 'borrowed',
            header: t('teamQuota.member.team.borrowed'),
            cell: row => row.borrowed,
          },
          {
            id: 'holders',
            header: t('teamQuota.lead.quota.holders'),
            cell: row =>
              row.holders.length
                ? row.holders
                    .map(h =>
                      t('teamQuota.lead.quota.holder', {
                        user: h.user,
                        running: h.running,
                        pending: h.pending,
                      }),
                    )
                    .join(', ')
                : '-',
          },
        ]}
      />
    </SpaceBetween>
  )
}
