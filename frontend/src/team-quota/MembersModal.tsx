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
  FormField,
  Input,
  Modal,
  RadioGroup,
  SpaceBetween,
  StatusIndicator,
  Table,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {notify} from '../model'
import {errorMessage} from './api'
import MemberEmailField from './MemberEmailField'
import {
  useAddTeamMemberMutation,
  useOffboardMemberMutation,
  useReenableMemberMutation,
  useRemoveTeamMemberMutation,
} from './queries'
import {MemberStep, OffboardJobs, OffboardStep, Team, TeamTarget} from './types'
import {
  userFromEmail,
  validateEmail,
  validateUserName,
  ValidationErrorKey,
} from './validation'
interface Props {
  team: Team | null
  target: TeamTarget | null
  visible: boolean
  onDismiss: () => void
  /** The team has an active lifecycle record: members are added by email (AD + PCUI + Slurm). */
  managed?: boolean
  emailDomains?: string[]
}
type Steps = (MemberStep | OffboardStep)[]
const STEP_IDS = ['ad', 'cognito', 'slurm', 'envs'] as const
type StepId = typeof STEP_IDS[number]
export default function MembersModal({
  team,
  target,
  visible,
  onDismiss,
  managed = false,
  emailDomains = [],
}: Props) {
  const {t} = useTranslation()
  const addMutation = useAddTeamMemberMutation(target)
  const removeMutation = useRemoveTeamMemberMutation(target)
  const offboardMutation = useOffboardMemberMutation(target)
  const reenableMutation = useReenableMemberMutation(target)
  const [value, setValue] = React.useState('')
  const [linkExisting, setLinkExisting] = React.useState(false)
  const [error, setError] = React.useState<ValidationErrorKey | null>(null)
  const [failure, setFailure] = React.useState<string | null>(null)
  const [steps, setSteps] = React.useState<{
    header: string
    steps: Steps
  } | null>(null)
  const [toOffboard, setToOffboard] = React.useState<string | null>(null)
  const [jobs, setJobs] = React.useState<OffboardJobs>('finish')
  React.useEffect(() => {
    if (!visible) return
    setValue('')
    setLinkExisting(false)
    setError(null)
    setSteps(null)
    setToOffboard(null)
  }, [visible])
  if (!team) return null
  const offboarded = team.offboarded || {}
  // Offboarded members whose Slurm association is already gone are still listed, so they can be re-enabled.
  const rows = [
    ...team.members,
    ...Object.keys(offboarded)
      .filter(user => !team.members.includes(user))
      .sort(),
  ]
  const busy =
    removeMutation.isLoading ||
    offboardMutation.isLoading ||
    reenableMutation.isLoading
  const add = () => {
    const validation = managed
      ? validateEmail(value, emailDomains)
      : validateUserName(value)
    setError(validation)
    if (validation) return
    const member = managed
      ? {email: value.trim().toLowerCase(), link_existing: linkExisting}
      : {user: value}
    const label = managed ? userFromEmail(value)! : value
    addMutation.mutate(
      {team: team.name, member},
      {
        onSuccess: response => {
          notify(
            t('teamQuota.notifications.memberAdded', {
              user: label,
              team: team.name,
            }),
            'success',
          )
          setSteps(
            response.steps
              ? {
                  header: t('teamQuota.members.stepsHeader'),
                  steps: response.steps,
                }
              : null,
          )
          setValue('')
          setLinkExisting(false)
        },
        onError: err => setFailure(errorMessage(err)),
      },
    )
  }
  const remove = (member: string) =>
    removeMutation.mutate(
      {team: team.name, user: member},
      {
        onSuccess: () =>
          notify(
            t('teamQuota.notifications.memberRemoved', {
              user: member,
              team: team.name,
            }),
            'success',
          ),
        onError: err => setFailure(errorMessage(err)),
      },
    )
  const offboard = () => {
    const user = toOffboard
    if (!user) return
    setToOffboard(null)
    offboardMutation.mutate(
      {team: team.name, user, jobs},
      {
        onSuccess: response => {
          notify(
            t(`teamQuota.offboard.done.${response.state}`, {user}),
            'success',
          )
          setSteps({
            header: t('teamQuota.offboard.stepsHeader', {user}),
            steps: response.steps,
          })
        },
        onError: err => setFailure(errorMessage(err)),
      },
    )
  }
  const reenable = (user: string) =>
    reenableMutation.mutate(
      {team: team.name, user},
      {
        onSuccess: response => {
          notify(t('teamQuota.offboard.reenabled', {user}), 'success')
          setSteps({
            header: t('teamQuota.offboard.reenableStepsHeader', {user}),
            steps: response.steps,
          })
        },
        onError: err => setFailure(errorMessage(err)),
      },
    )
  const stepLabel = (id: string) =>
    (STEP_IDS as readonly string[]).includes(id)
      ? t(`teamQuota.members.steps.${id as StepId}`)
      : id
  return (
    <>
      <Modal
        visible={visible}
        onDismiss={onDismiss}
        size="medium"
        header={t('teamQuota.members.title', {team: team.name})}
        footer={
          <Box float="right">
            <Button onClick={onDismiss}>{t('teamQuota.actions.close')}</Button>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {failure && (
            <Alert type="error" dismissible onDismiss={() => setFailure(null)}>
              {failure}
            </Alert>
          )}
          <Table
            variant="embedded"
            items={rows.map(member => ({member}))}
            trackBy="member"
            empty={t('teamQuota.members.empty')}
            columnDefinitions={[
              {
                id: 'member',
                header: t('teamQuota.members.user'),
                cell: item => item.member,
              },
              {
                id: 'state',
                header: t('teamQuota.members.state'),
                cell: item =>
                  offboarded[item.member] ? (
                    <StatusIndicator type="stopped">
                      {t(
                        `teamQuota.offboard.states.${offboarded[item.member]}`,
                      )}
                    </StatusIndicator>
                  ) : (
                    <StatusIndicator type="success">
                      {t('teamQuota.offboard.states.active')}
                    </StatusIndicator>
                  ),
              },
              {
                id: 'actions',
                header: '',
                cell: item => (
                  <SpaceBetween direction="horizontal" size="xs">
                    {managed && !offboarded[item.member] && (
                      <Button
                        variant="link"
                        ariaLabel={t('teamQuota.offboard.offboardAria', {
                          user: item.member,
                        })}
                        disabled={busy}
                        onClick={() => {
                          setJobs('finish')
                          setToOffboard(item.member)
                        }}
                      >
                        {t('teamQuota.offboard.offboard')}
                      </Button>
                    )}
                    {managed && offboarded[item.member] && (
                      <Button
                        variant="link"
                        ariaLabel={t('teamQuota.offboard.reenableAria', {
                          user: item.member,
                        })}
                        disabled={busy}
                        onClick={() => reenable(item.member)}
                      >
                        {t('teamQuota.offboard.reenable')}
                      </Button>
                    )}
                    {team.members.includes(item.member) && (
                      <Button
                        variant="link"
                        ariaLabel={t('teamQuota.members.removeAria', {
                          user: item.member,
                        })}
                        disabled={busy}
                        onClick={() => remove(item.member)}
                      >
                        {t('teamQuota.actions.remove')}
                      </Button>
                    )}
                  </SpaceBetween>
                ),
              },
            ]}
          />
          {managed ? (
            <MemberEmailField
              label={t('teamQuota.members.addEmailLabel')}
              ariaLabel={t('teamQuota.members.addEmailLabel')}
              email={value}
              linkExisting={linkExisting}
              domains={emailDomains}
              error={error}
              onEmailChange={email => {
                setValue(email)
                setError(null)
              }}
              onLinkExistingChange={setLinkExisting}
              secondaryControl={
                <Button loading={addMutation.isLoading} onClick={add}>
                  {t('teamQuota.actions.addMember')}
                </Button>
              }
            />
          ) : (
            <FormField
              label={t('teamQuota.members.addLabel')}
              description={t('teamQuota.members.addDescription')}
              errorText={error && t(error)}
              secondaryControl={
                <Button loading={addMutation.isLoading} onClick={add}>
                  {t('teamQuota.actions.addMember')}
                </Button>
              }
            >
              <Input
                ariaLabel={t('teamQuota.members.addLabel')}
                value={value}
                onChange={({detail}) => setValue(detail.value)}
              />
            </FormField>
          )}
          {managed && (
            <Box variant="small">
              {t('teamQuota.members.addEmailDescription')}
            </Box>
          )}
          {steps && (
            <Alert type="success" header={steps.header}>
              {steps.steps
                .map(
                  step =>
                    `${stepLabel(step.id)}: ${t(
                      `teamQuota.lifecycle.statuses.${step.status}`,
                    )}`,
                )
                .join(' · ')}
            </Alert>
          )}
        </SpaceBetween>
      </Modal>
      <Modal
        visible={!!toOffboard}
        onDismiss={() => setToOffboard(null)}
        header={t('teamQuota.offboard.title', {user: toOffboard})}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setToOffboard(null)}>
                {t('teamQuota.actions.cancel')}
              </Button>
              <Button variant="primary" onClick={offboard}>
                {t('teamQuota.offboard.confirm')}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>{t('teamQuota.offboard.body', {user: toOffboard})}</Box>
          <RadioGroup
            value={jobs}
            onChange={({detail}) => setJobs(detail.value as OffboardJobs)}
            items={[
              {
                value: 'finish',
                label: t('teamQuota.offboard.finish'),
                description: t('teamQuota.offboard.finishDescription'),
              },
              {
                value: 'cancel',
                label: t('teamQuota.offboard.cancel'),
                description: t('teamQuota.offboard.cancelDescription'),
              },
            ]}
          />
        </SpaceBetween>
      </Modal>
    </>
  )
}
