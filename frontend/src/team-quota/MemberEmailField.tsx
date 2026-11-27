// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {
  Checkbox,
  ExpandableSection,
  FormField,
  Input,
  SpaceBetween,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {userFromEmail, ValidationErrorKey} from './validation'

/** Error shown while typing: only the missing '@' (the full check runs on submit). */
export function inlineEmailError(email: string): ValidationErrorKey | null {
  const value = (email ?? '').trim()
  return value && !value.includes('@')
    ? 'teamQuota.errors.emailMissingAt'
    : null
}

export function emailHint(
  t: (key: string, options?: any) => string,
  domains: string[],
) {
  return (
    t('teamQuota.memberEmail.hint') +
    (domains.length
      ? t('teamQuota.memberEmail.allowedDomains', {domains: domains.join(', ')})
      : '')
  )
}

/**
 * A member email: full address with the example hint, the derived cluster user, an inline error and the
 * "Link existing AD user" option folded under Advanced (unchecked by default).
 */
export default function MemberEmailField({
  label,
  ariaLabel,
  email,
  linkExisting,
  domains,
  error,
  onEmailChange,
  onLinkExistingChange,
  secondaryControl,
}: {
  label?: string
  ariaLabel?: string
  email: string
  linkExisting: boolean
  domains: string[]
  error?: ValidationErrorKey | null
  onEmailChange: (value: string) => void
  onLinkExistingChange: (value: boolean) => void
  secondaryControl?: React.ReactNode
}) {
  const {t} = useTranslation()
  const shown = error || inlineEmailError(email)
  const user = userFromEmail(email)
  return (
    <SpaceBetween size="xs">
      <FormField
        label={label}
        description={emailHint(t, domains)}
        constraintText={t('teamQuota.lifecycle.clusterUser', {
          user: user || '-',
        })}
        errorText={shown && t(shown)}
        secondaryControl={secondaryControl}
      >
        <Input
          type="email"
          ariaLabel={ariaLabel}
          placeholder="zhangsan@example.com"
          value={email}
          onChange={({detail}) => onEmailChange(detail.value)}
        />
      </FormField>
      <ExpandableSection headerText={t('teamQuota.memberEmail.advanced')}>
        <FormField description={t('teamQuota.memberEmail.linkExistingHint')}>
          <Checkbox
            checked={linkExisting}
            onChange={({detail}) => onLinkExistingChange(detail.checked)}
          >
            {t('teamQuota.lifecycle.linkExisting')}
          </Checkbox>
        </FormField>
      </ExpandableSection>
    </SpaceBetween>
  )
}
