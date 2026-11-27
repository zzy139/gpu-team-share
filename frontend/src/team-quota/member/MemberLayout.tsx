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
  FormField,
  Select,
  SpaceBetween,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {BreadcrumbGroupNavigate} from '../../old-pages/Configure/Components'
import Layout from '../../old-pages/Layout'
import {errorMessage} from '../api'
import BudgetAlert from '../BudgetAlert'
import PageNotifications from '../PageNotifications'
import NotificationsBell from './NotificationsBell'
import {useMemberBudgetQuery, useMemberTarget} from './queries'
export type MemberPageKey =
  | 'myJobs'
  | 'submitJob'
  | 'myTeam'
  | 'envs'
  | 'templates'
const HREF: Record<MemberPageKey, string> = {
  myJobs: '/my-jobs',
  submitJob: '/submit',
  myTeam: '/my-team',
  envs: '/envs',
  templates: '/templates',
}
export function MemberTeamSelect() {
  const {t} = useTranslation()
  const {target, teams, setTeam} = useMemberTarget()
  if (teams.length < 2) return null
  return (
    <FormField label={t('teamQuota.member.teamLabel')}>
      <Select
        selectedOption={
          target?.team ? {label: target.team, value: target.team} : null
        }
        options={teams.map(team => ({label: team, value: team}))}
        onChange={({detail}) => setTeam(detail.selectedOption.value!)}
      />
    </FormField>
  )
}
export default function MemberLayout({
  page,
  extraCrumb,
  children,
}: {
  page: MemberPageKey
  extraCrumb?: {text: string; href: string}
  children: React.ReactNode
}) {
  const {t} = useTranslation()
  const {error, target, isLoading} = useMemberTarget()
  const budget = useMemberBudgetQuery(target)
  const crumbs = [
    {text: t('global.menu.header'), href: '/'},
    {text: t(`global.menu.${page}`), href: HREF[page]},
    ...(extraCrumb ? [extraCrumb] : []),
  ]
  return (
    <Layout
      contentType="default"
      breadcrumbs={<BreadcrumbGroupNavigate items={crumbs} />}
      notifications={
        <PageNotifications>
          {!!error && (
            <Alert type="error" header={t('teamQuota.member.identityError')}>
              {errorMessage(error)}
            </Alert>
          )}
          {!error && !isLoading && !target && (
            <Alert type="warning">{t('teamQuota.member.noTeam')}</Alert>
          )}
          <BudgetAlert banner={budget.data?.budget} />
        </PageNotifications>
      }
    >
      <SpaceBetween size="l">
        {target && (
          <Box float="right">
            <NotificationsBell target={target} />
          </Box>
        )}
        {children}
      </SpaceBetween>
    </Layout>
  )
}
