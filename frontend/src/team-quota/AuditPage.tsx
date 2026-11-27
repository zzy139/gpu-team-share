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
  FormField,
  Header,
  Input,
  StatusIndicator,
  Table,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import EmptyState from '../components/EmptyState'
import Layout from '../old-pages/Layout'
import {errorMessage} from './api'
import PageNotifications from './PageNotifications'
import {useAuditQuery} from './queries'
import {TeamBreadcrumbs} from './TeamsPage'
import {AuditEntry, AuditFilters} from './types'
import {NAME_RE} from './validation'

const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const ACTION_RE = /^[a-z][a-z0-9_]{0,63}$/

interface Draft {
  month: string
  from: string
  to: string
  actor: string
  team: string
  action: string
}
const EMPTY: Draft = {
  month: '',
  from: '',
  to: '',
  actor: '',
  team: '',
  action: '',
}

/** Filters to send, or the field errors. */
export function auditFilters(draft: Draft): {
  filters: AuditFilters | null
  errors: Partial<Record<keyof Draft, boolean>>
} {
  const trimmed = Object.fromEntries(
    Object.entries(draft).map(([k, v]) => [k, v.trim()]),
  ) as unknown as Draft
  const errors: Partial<Record<keyof Draft, boolean>> = {
    month: !!trimmed.month && !MONTH_RE.test(trimmed.month),
    from: !!trimmed.from && !DATE_RE.test(trimmed.from),
    to: !!trimmed.to && !DATE_RE.test(trimmed.to),
    actor: trimmed.actor.length > 128,
    team: !!trimmed.team && !NAME_RE.test(trimmed.team),
    action: !!trimmed.action && !ACTION_RE.test(trimmed.action),
  }
  if (Object.values(errors).some(Boolean)) return {filters: null, errors}
  const filters: AuditFilters = {}
  ;(Object.keys(trimmed) as (keyof Draft)[]).forEach(key => {
    if (trimmed[key]) filters[key] = trimmed[key]
  })
  if (filters.month) {
    delete filters.from
    delete filters.to
  }
  return {filters, errors}
}

export default function AuditPage() {
  const {t} = useTranslation()
  const [draft, setDraft] = React.useState<Draft>(EMPTY)
  const [filters, setFilters] = React.useState<AuditFilters>({})
  const [errors, setErrors] = React.useState<
    Partial<Record<keyof Draft, boolean>>
  >({})
  const {data, isLoading, isError, error, refetch, isFetching} =
    useAuditQuery(filters)
  const apply = () => {
    const result = auditFilters(draft)
    setErrors(result.errors)
    if (result.filters) setFilters(result.filters)
  }
  const field = (key: keyof Draft, placeholder?: string) => (
    <FormField
      label={t(`teamQuota.audit.filters.${key}`)}
      errorText={errors[key] ? t(`teamQuota.audit.errors.${key}`) : undefined}
    >
      <Input
        ariaLabel={t(`teamQuota.audit.filters.${key}`)}
        placeholder={placeholder}
        value={draft[key]}
        onChange={({detail}) =>
          setDraft(prev => ({...prev, [key]: detail.value}))
        }
        onKeyDown={({detail}) => detail.key === 'Enter' && apply()}
      />
    </FormField>
  )
  return (
    <Layout
      contentType="table"
      breadcrumbs={
        <TeamBreadcrumbs titleKey="global.menu.audit" href="/audit" />
      }
      notifications={
        <PageNotifications>
          {isError && (
            <Alert type="error" header={t('teamQuota.audit.loadError')}>
              {errorMessage(error)}
            </Alert>
          )}
          {data?.truncated && (
            <Alert type="info">{t('teamQuota.audit.truncated')}</Alert>
          )}
        </PageNotifications>
      }
    >
      <Table<AuditEntry>
        variant="full-page"
        stickyHeader
        resizableColumns
        wrapLines
        trackBy={item => `${item.time}/${item.action}/${item.actor}`}
        loading={isLoading}
        loadingText={t('teamQuota.audit.loading')}
        items={data?.entries || []}
        filter={
          <ColumnLayout columns={4}>
            {field('month', 'YYYY-MM')}
            {field('from', 'YYYY-MM-DD')}
            {field('to', 'YYYY-MM-DD')}
            {field('actor')}
            {field('team')}
            {field('action', 'env_create')}
            <FormField label="&nbsp;">
              <Button onClick={apply}>{t('teamQuota.audit.apply')}</Button>
            </FormField>
          </ColumnLayout>
        }
        empty={
          <EmptyState
            title={t('teamQuota.audit.empty.title')}
            subtitle={t('teamQuota.audit.empty.subtitle')}
          />
        }
        header={
          <Header
            variant="awsui-h1-sticky"
            counter={`(${data?.entries.length ?? 0})`}
            description={t('teamQuota.audit.description', {
              months: (data?.months || []).join(', ') || '-',
            })}
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
            {t('teamQuota.audit.title')}
          </Header>
        }
        columnDefinitions={[
          {
            id: 'time',
            header: t('teamQuota.audit.columns.time'),
            cell: e => e.time,
          },
          {
            id: 'actor',
            header: t('teamQuota.audit.columns.actor'),
            cell: e => e.actor_email || e.actor || '-',
          },
          {
            id: 'action',
            header: t('teamQuota.audit.columns.action'),
            cell: e => e.action,
          },
          {
            id: 'team',
            header: t('teamQuota.audit.columns.team'),
            cell: e => e.team || '-',
          },
          {
            id: 'cluster',
            header: t('teamQuota.audit.columns.cluster'),
            cell: e => e.cluster || '-',
          },
          {
            id: 'result',
            header: t('teamQuota.audit.columns.result'),
            cell: e => (
              <StatusIndicator
                type={
                  e.result === 'success'
                    ? 'success'
                    : e.result === 'rejected'
                    ? 'warning'
                    : 'error'
                }
              >
                {e.result}
              </StatusIndicator>
            ),
          },
          {
            id: 'params',
            header: t('teamQuota.audit.columns.params'),
            cell: e => (
              <Box variant="code" fontSize="body-s">
                {e.params || '-'}
              </Box>
            ),
          },
          {
            id: 'error',
            header: t('teamQuota.audit.columns.error'),
            cell: e => e.error || '-',
          },
        ]}
      />
    </Layout>
  )
}
