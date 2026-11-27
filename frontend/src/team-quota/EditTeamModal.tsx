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
  Form,
  FormField,
  Input,
  Modal,
  SpaceBetween,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {notify} from '../model'
import {errorMessage} from './api'
import FairshareSection from './FairshareField'
import {useUpdateTeamMutation} from './queries'
import {
  CatalogType,
  Team,
  TeamTarget,
  UnmanagedQueue,
  UpdateTeamRequest,
} from './types'
import {
  initialTypeValues,
  MultiTypeQueuesAlert,
  typeLimitsFrom,
  TypeLimitsTable,
  TypeLimitValues,
  validateTypeValues,
} from './TypeLimitsTable'
import {
  BUDGET_USD,
  ENV_CAP_GB,
  FAIRSHARE,
  parseAmount,
  parseInteger,
  validateInteger,
  validateOptionalAmount,
  validateOptionalInteger,
  ValidationErrorKey,
} from './validation'

interface Props {
  team: Team | null
  target: TeamTarget | null
  catalog: CatalogType[]
  /** Every team of the cluster: the live fairshare share is computed against the others. */
  teams?: Team[]
  unmanagedQueues?: UnmanagedQueue[]
  /** Lifecycle-managed team: the environment storage cap can be set. */
  managed?: boolean
  visible: boolean
  onDismiss: () => void
}

type Errors = Record<string, ValidationErrorKey | null>

export default function EditTeamModal({
  team,
  target,
  catalog,
  teams = [],
  unmanagedQueues,
  managed = false,
  visible,
  onDismiss,
}: Props) {
  const {t} = useTranslation()
  const mutation = useUpdateTeamMutation(target)
  const [fairshare, setFairshare] = React.useState('')
  const [failure, setFailure] = React.useState<string | null>(null)
  const [types, setTypes] = React.useState<TypeLimitValues>({})
  const [budget, setBudget] = React.useState('')
  const [envCap, setEnvCap] = React.useState('')
  const [errors, setErrors] = React.useState<Errors>({})
  const otherFairshares = teams
    .filter(other => other.name !== team?.name)
    .map(other => other.fairshare || 0)

  React.useEffect(() => {
    if (!team || !visible) return
    setFairshare(team.fairshare?.toString() ?? '')
    setTypes(initialTypeValues(catalog, team.type_limits))
    setBudget(team.budget_usd ? String(team.budget_usd) : '')
    setEnvCap(team.env_storage_cap_gb ? String(team.env_storage_cap_gb) : '')
    setErrors({})
  }, [team, visible, catalog])

  const submit = () => {
    if (!team) return
    const newErrors: Errors = {
      ...validateTypeValues(catalog, types),
      fairshare: validateInteger(fairshare, FAIRSHARE),
      budget: validateOptionalAmount(budget, BUDGET_USD),
      envCap: managed ? validateOptionalInteger(envCap, ENV_CAP_GB) : null,
    }
    setErrors(newErrors)
    if (Object.values(newErrors).some(Boolean)) return

    // Every catalog type is sent: 0 removes that type's QOS (running jobs keep running).
    const body: UpdateTeamRequest = {
      fairshare: parseInteger(fairshare)!,
      budget_usd: parseAmount(budget) || null,
    }
    if (catalog.length) body.type_limits = typeLimitsFrom(catalog, types)
    if (managed) body.env_storage_cap_gb = parseInteger(envCap) || null
    mutation.mutate(
      {team: team.name, body},
      {
        onSuccess: () => {
          notify(
            t('teamQuota.notifications.updated', {team: team.name}),
            'success',
          )
          onDismiss()
        },
        onError: error => setFailure(errorMessage(error)),
      },
    )
  }

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      size="large"
      header={t('teamQuota.edit.title', {team: team?.name})}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>{t('teamQuota.actions.cancel')}</Button>
            <Button
              variant="primary"
              loading={mutation.isLoading}
              onClick={submit}
            >
              {t('teamQuota.actions.save')}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {failure && (
            <Alert type="error" dismissible onDismiss={() => setFailure(null)}>
              {failure}
            </Alert>
          )}
          <MultiTypeQueuesAlert queues={unmanagedQueues} />
          <FormField
            label={t('teamQuota.fields.typeLimits')}
            description={t('teamQuota.fields.typeLimitsEditDescription')}
            stretch
          >
            <TypeLimitsTable
              catalog={catalog}
              values={types}
              errors={errors}
              onChange={(type, value) =>
                setTypes(prev => ({...prev, [type]: value}))
              }
            />
          </FormField>
          <FormField
            label={t('teamQuota.fields.budget')}
            description={t('teamQuota.fields.budgetDescription')}
            errorText={errors.budget && t(errors.budget, BUDGET_USD)}
          >
            <Input
              type="number"
              ariaLabel={t('teamQuota.fields.budget')}
              value={budget}
              onChange={({detail}) => setBudget(detail.value)}
            />
          </FormField>
          {managed && (
            <FormField
              label={t('teamQuota.fields.envCap')}
              description={t('teamQuota.fields.envCapDescription')}
              errorText={errors.envCap && t(errors.envCap, ENV_CAP_GB)}
            >
              <Input
                type="number"
                ariaLabel={t('teamQuota.fields.envCap')}
                value={envCap}
                onChange={({detail}) => setEnvCap(detail.value)}
              />
            </FormField>
          )}
          <FairshareSection
            value={fairshare}
            onChange={setFairshare}
            error={errors.fairshare}
            otherFairshares={otherFairshares}
          />
        </SpaceBetween>
      </Form>
    </Modal>
  )
}
