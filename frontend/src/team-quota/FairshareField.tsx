// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {
  ExpandableSection,
  FormField,
  Input,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {formatPercent} from './format'
import {FAIRSHARE, parseInteger, ValidationErrorKey} from './validation'

export const DEFAULT_FAIRSHARE = '50'

/** Share of `value` in the sum of the other teams' fairshare plus `value`; null when `value` is not a valid number. */
export function fairshareShare(
  value: string,
  otherFairshares: number[],
): number | null {
  const own = parseInteger(value)
  if (own === null || own < FAIRSHARE.min || own > FAIRSHARE.max) return null
  const total = otherFairshares.reduce((sum, v) => sum + (v || 0), own)
  return total > 0 ? own / total : null
}

/** Fairshare under 高级选项 (collapsed unless it has an error), with the live share among all teams. */
export default function FairshareSection({
  value,
  onChange,
  error,
  otherFairshares,
}: {
  value: string
  onChange: (value: string) => void
  error: ValidationErrorKey | null | undefined
  otherFairshares: number[]
}) {
  const {t} = useTranslation()
  const [expanded, setExpanded] = React.useState(false)
  const share = fairshareShare(value, otherFairshares)
  return (
    <ExpandableSection
      headerText={t('teamQuota.fields.advanced')}
      expanded={expanded || !!error}
      onChange={({detail}) => setExpanded(detail.expanded)}
    >
      <FormField
        label={t('teamQuota.fields.fairshare')}
        description={t('teamQuota.fields.fairshareDescription')}
        constraintText={
          share === null
            ? undefined
            : t('teamQuota.fields.fairshareShare', {
                percent: formatPercent(share),
              })
        }
        errorText={error && t(error, FAIRSHARE)}
      >
        <Input
          type="number"
          ariaLabel={t('teamQuota.fields.fairshare')}
          value={value}
          onChange={({detail}) => onChange(detail.value)}
        />
      </FormField>
    </ExpandableSection>
  )
}
