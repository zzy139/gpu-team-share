// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {Alert} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {formatDollars} from './format'
import {BudgetBanner} from './types'

/** Month-to-date estimate against the team budget, shown from 80 % on. */
export default function BudgetAlert({banner}: {banner?: BudgetBanner | null}) {
  const {t} = useTranslation()
  if (!banner?.level) return null
  return (
    <Alert
      type={banner.level === 'exceeded' ? 'error' : 'warning'}
      header={t(`teamQuota.budget.${banner.level}`, {team: banner.team})}
    >
      {t('teamQuota.budget.body', {
        spent: formatDollars(banner.spent_usd),
        budget: formatDollars(banner.budget_usd),
        percent: banner.percent ?? '-',
        month: banner.month,
      })}
    </Alert>
  )
}
