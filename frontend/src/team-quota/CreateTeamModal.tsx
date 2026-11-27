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
  Container,
  Form,
  FormField,
  Header,
  Input,
  Modal,
  SpaceBetween,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {notify} from '../model'
import {errorMessage} from './api'
import FairshareSection, {DEFAULT_FAIRSHARE} from './FairshareField'
import MemberEmailField from './MemberEmailField'
import {useCreateLifecycleTeamMutation, useCreateTeamMutation} from './queries'
import {
  CatalogType,
  CreateLifecycleTeamRequest,
  CreateTeamRequest,
  LifecycleListResponse,
  Team,
  TeamTarget,
  UnmanagedQueue,
} from './types'
import {
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
  MAX_LIFECYCLE_MEMBERS,
  parseAmount,
  parseInteger,
  userFromEmail,
  validateEmail,
  validateInteger,
  validateLifecycleTeamName,
  validateOptionalAmount,
  validateOptionalInteger,
  ValidationErrorKey,
  validateTeamName,
} from './validation'

interface Props {
  target: TeamTarget | null
  /** Instance types that team quotas can count (one instance type per queue). */
  catalog: CatalogType[]
  /** Existing teams: the live fairshare share is computed against them. */
  teams?: Team[]
  unmanagedQueues?: UnmanagedQueue[]
  visible: boolean
  onDismiss: () => void
  /** When the lifecycle stack is configured, creating a team provisions AD, EFS, ECR, Cognito and Slurm. */
  lifecycle?: LifecycleListResponse
  onProvision?: (team: string) => void
}

interface MemberRow {
  email: string
  linkExisting: boolean
}

type Errors = Record<string, ValidationErrorKey | null>

export default function CreateTeamModal({
  target,
  catalog,
  teams = [],
  unmanagedQueues,
  visible,
  onDismiss,
  lifecycle,
  onProvision,
}: Props) {
  const {t} = useTranslation()
  const legacyMutation = useCreateTeamMutation(target)
  const lifecycleMutation = useCreateLifecycleTeamMutation(target)
  const managed = !!lifecycle?.configured
  const domains = lifecycle?.email_domains || []
  const [members, setMembers] = React.useState<MemberRow[]>([])
  const [failure, setFailure] = React.useState<string | null>(null)
  const [lead, setLead] = React.useState('')
  const [name, setName] = React.useState('')
  const [fairshare, setFairshare] = React.useState(DEFAULT_FAIRSHARE)
  const [types, setTypes] = React.useState<TypeLimitValues>({})
  const [budget, setBudget] = React.useState('')
  const [envCap, setEnvCap] = React.useState('')
  const [errors, setErrors] = React.useState<Errors>({})
  const otherFairshares = teams.map(team => team.fairshare || 0)

  React.useEffect(() => {
    if (!visible) return
    setName('')
    setFairshare(DEFAULT_FAIRSHARE)
    setTypes({})
    setBudget('')
    setEnvCap('')
    setErrors({})
    setMembers([])
    setLead('')
  }, [visible])

  const updateMember = (index: number, change: Partial<MemberRow>) =>
    setMembers(rows =>
      rows.map((row, i) => (i === index ? {...row, ...change} : row)),
    )

  const submit = () => {
    const typeErrors = validateTypeValues(catalog, types)
    const typeLimits = typeLimitsFrom(catalog, types)
    const allowed = Object.fromEntries(
      Object.entries(typeLimits).filter(([, machines]) => machines > 0),
    )
    const newErrors: Errors = {
      ...typeErrors,
      name: managed ? validateLifecycleTeamName(name) : validateTeamName(name),
      fairshare: validateInteger(fairshare, FAIRSHARE),
      budget: validateOptionalAmount(budget, BUDGET_USD),
      envCap: managed ? validateOptionalInteger(envCap, ENV_CAP_GB) : null,
      types:
        Object.values(typeErrors).some(Boolean) ||
        Object.keys(allowed).length > 0
          ? null
          : 'teamQuota.errors.noTypeAllowed',
    }
    const seen = new Set<string>()
    if (managed) {
      members.forEach((row, index) => {
        const key = `member-${index}`
        newErrors[key] = validateEmail(row.email, domains)
        const user = userFromEmail(row.email)
        if (!newErrors[key] && user && seen.has(user))
          newErrors[key] = 'teamQuota.errors.duplicateEmail'
        if (user) seen.add(user)
      })
      if (lead.trim()) newErrors.lead = validateEmail(lead, domains)
    }
    setErrors(newErrors)
    if (Object.values(newErrors).some(Boolean)) return

    const body: CreateTeamRequest = {
      name,
      fairshare: parseInteger(fairshare)!,
      type_limits: allowed,
    }
    const budgetValue = parseAmount(budget)
    if (budgetValue) body.budget_usd = budgetValue
    if (managed) {
      const lifecycleBody: CreateLifecycleTeamRequest = {
        ...body,
        members: members.map(row => ({
          email: row.email.trim().toLowerCase(),
          link_existing: row.linkExisting,
        })),
      }
      const cap = parseInteger(envCap)
      if (cap) lifecycleBody.env_storage_cap_gb = cap
      if (lead.trim()) lifecycleBody.lead = lead.trim().toLowerCase()
      lifecycleMutation.mutate(lifecycleBody, {
        onSuccess: () => {
          onDismiss()
          onProvision?.(name)
        },
        onError: error => setFailure(errorMessage(error)),
      })
      return
    }
    legacyMutation.mutate(body, {
      onSuccess: () => {
        notify(t('teamQuota.notifications.created', {team: name}), 'success')
        onDismiss()
      },
      onError: error => setFailure(errorMessage(error)),
    })
  }

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      size="large"
      header={t('teamQuota.create.title')}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>{t('teamQuota.actions.cancel')}</Button>
            <Button
              variant="primary"
              loading={legacyMutation.isLoading || lifecycleMutation.isLoading}
              onClick={submit}
            >
              {t('teamQuota.actions.create')}
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
            label={t('teamQuota.fields.name')}
            description={t(
              managed
                ? 'teamQuota.fields.lifecycleNameDescription'
                : 'teamQuota.fields.nameDescription',
            )}
            errorText={errors.name && t(errors.name)}
          >
            <Input
              ariaLabel={t('teamQuota.fields.name')}
              value={name}
              onChange={({detail}) => setName(detail.value)}
            />
          </FormField>
          <FormField
            label={t('teamQuota.fields.typeLimits')}
            description={t('teamQuota.fields.typeLimitsDescription')}
            errorText={errors.types && t(errors.types)}
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
          {managed && (
            <FormField
              label={t('teamQuota.lifecycle.lead')}
              description={t('teamQuota.lifecycle.leadDescription')}
              errorText={errors.lead && t(errors.lead)}
            >
              <Input
                type="email"
                ariaLabel={t('teamQuota.lifecycle.lead')}
                value={lead}
                onChange={({detail}) => setLead(detail.value)}
              />
            </FormField>
          )}
          {managed && (
            <Container
              header={
                <Header
                  variant="h3"
                  description={t('teamQuota.lifecycle.membersDescription')}
                >
                  {t('teamQuota.lifecycle.members')}
                </Header>
              }
            >
              <SpaceBetween size="m">
                {members.map((row, index) => (
                  <MemberEmailField
                    key={index}
                    label={t('teamQuota.lifecycle.memberEmail', {
                      index: index + 1,
                    })}
                    ariaLabel={t('teamQuota.lifecycle.memberEmail', {
                      index: index + 1,
                    })}
                    email={row.email}
                    linkExisting={row.linkExisting}
                    domains={domains}
                    error={errors[`member-${index}`]}
                    onEmailChange={email => updateMember(index, {email})}
                    onLinkExistingChange={linkExisting =>
                      updateMember(index, {linkExisting})
                    }
                    secondaryControl={
                      <Button
                        variant="link"
                        onClick={() =>
                          setMembers(rows => rows.filter((_, i) => i !== index))
                        }
                      >
                        {t('teamQuota.actions.remove')}
                      </Button>
                    }
                  />
                ))}
                <Button
                  disabled={members.length >= MAX_LIFECYCLE_MEMBERS}
                  onClick={() =>
                    setMembers(rows => [
                      ...rows,
                      {email: '', linkExisting: false},
                    ])
                  }
                >
                  {t('teamQuota.lifecycle.addMemberRow')}
                </Button>
              </SpaceBetween>
            </Container>
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
