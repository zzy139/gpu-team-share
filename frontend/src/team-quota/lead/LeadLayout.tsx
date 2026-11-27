// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {SpaceBetween} from '@cloudscape-design/components'
import React from 'react'
import {BreadcrumbGroupNavigate} from '../../old-pages/Configure/Components'
import Layout from '../../old-pages/Layout'
import {useTranslation} from 'react-i18next'
import PageNotifications from '../PageNotifications'
import {LeadTeamSelect} from './LeadTeam'

export type LeadPageKey = 'envs'
const HREF: Record<LeadPageKey, string> = {
  envs: '/envs',
}

/** Pages of team leads (and admins): cluster + team select above the content, alerts in the notifications slot. */
export default function LeadLayout({
  page,
  alerts,
  children,
}: {
  page: LeadPageKey
  alerts?: React.ReactNode
  children: React.ReactNode
}) {
  const {t} = useTranslation()
  return (
    <Layout
      contentType="default"
      breadcrumbs={
        <BreadcrumbGroupNavigate
          items={[
            {text: t('global.menu.header'), href: '/'},
            {text: t(`global.menu.${page}`), href: HREF[page]},
          ]}
        />
      }
      notifications={<PageNotifications>{alerts}</PageNotifications>}
    >
      <SpaceBetween size="l">
        <LeadTeamSelect />
        {children}
      </SpaceBetween>
    </Layout>
  )
}
