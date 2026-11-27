// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {
  SideNavigation,
  SideNavigationProps,
} from '@cloudscape-design/components'
import * as React from 'react'
import {useTranslation} from 'react-i18next'
import {useLocation, useNavigate} from 'react-router-dom'
import {useState} from '../store'
import {hasMemberRole, isAdmin, teamsFromRoles} from '../team-quota/roles'

type MenuKey =
  | 'clusters'
  | 'capacity'
  | 'images'
  | 'teams'
  | 'teamUsage'
  | 'users'
  | 'audit'
  | 'myTeam'
  | 'myJobs'
  | 'submitJob'
  | 'envs'

export default function SideBar() {
  const {t} = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()

  const activeHref = '/' + location.pathname.split('/')?.[1]
  const identity = useState(['identity'])
  const admin = isAdmin(identity)
  const lead = teamsFromRoles(identity).length > 0
  const member = hasMemberRole(identity)

  const header = React.useMemo(
    () => ({
      href: admin ? '/clusters' : lead ? '/my-team' : '/my-jobs',
      text: t('global.menu.header'),
    }),
    [t, admin, lead],
  )

  const navigationItems: ReadonlyArray<SideNavigationProps.Item> =
    React.useMemo(() => {
      const link = (key: MenuKey, href: string): SideNavigationProps.Link => ({
        type: 'link',
        text: t(`global.menu.${key}`),
        href,
      })
      // Groups of each role; a user with several roles gets their union, every page once.
      const groups: SideNavigationProps.Item[][] = []
      if (admin) {
        groups.push(
          [
            {
              type: 'section',
              text: t('global.menu.clusterSection'),
              items: [
                link('clusters', '/clusters'),
                link('capacity', '/capacity'),
                link('images', '/images'),
              ],
            },
          ],
          [link('teams', '/teams'), link('teamUsage', '/team-usage')],
          [link('users', '/users'), link('audit', '/audit')],
        )
      }
      if (lead) {
        groups.push([
          link('myTeam', '/my-team'),
          link('teamUsage', '/team-usage'),
        ])
      }
      if (member) {
        groups.push([
          link('myJobs', '/my-jobs'),
          link('submitJob', '/submit'),
          link('envs', '/envs'),
          link('myTeam', '/my-team'),
        ])
      }
      const seen = new Set<string>()
      const unique = groups
        .map(items =>
          items.filter(item => {
            if (item.type !== 'link') return true
            if (seen.has(item.href)) return false
            seen.add(item.href)
            return true
          }),
        )
        .filter(items => items.length > 0)
      return [
        ...unique.flatMap((items, index) =>
          index === 0
            ? items
            : [{type: 'divider'} as SideNavigationProps.Divider, ...items],
        ),
        {type: 'divider'},
        {
          type: 'link',
          text: t('global.menu.viewLicense'),
          href: '/pcui/license.txt',
          external: true,
        },
      ]
    }, [t, admin, lead, member])

  const onFollow = React.useCallback(
    event => {
      if (!event.detail.external) {
        event.preventDefault()
        navigate(event.detail.href)
      }
    },
    [navigate],
  )

  return (
    <SideNavigation
      header={header}
      activeHref={activeHref}
      onFollow={onFollow}
      items={navigationItems}
    />
  )
}
