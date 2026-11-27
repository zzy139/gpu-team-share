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
  Modal,
  SpaceBetween,
  StatusIndicator,
  StatusIndicatorProps,
  Table,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {useQueryClient} from 'react-query'
import {notify} from '../model'
import {AdvanceLifecycleTeam, errorMessage} from './api'
import {TEAM_QUOTA_QUERY_KEY} from './queries'
import {
  LifecycleStep,
  LifecycleStepId,
  LifecycleStepStatus,
  TeamTarget,
} from './types'

export const LIFECYCLE_STEPS: LifecycleStepId[] = [
  'ad',
  'storage',
  'ecr',
  'cognito',
  'slurm',
]
// A step the server reports as in progress is polled again after this delay.
export const IN_PROGRESS_WAIT_MS = 10000

const INDICATOR: Record<LifecycleStepStatus, StatusIndicatorProps.Type> = {
  done: 'success',
  pending: 'pending',
  in_progress: 'in-progress',
  failed: 'error',
}

/** No HTTP answer reached the browser (network drop) or the gateway gave up waiting (502/503/504): the step
 * may still have finished on the server. */
export function isConnectionError(err: unknown): boolean {
  const e = err as {
    response?: {status?: number}
    isAxiosError?: boolean
    message?: string
  }
  if (!e || typeof e !== 'object') return false
  if (!e.response)
    return e.isAxiosError === true || e.message === 'Network Error'
  return [502, 503, 504].includes(e.response.status || 0)
}

type Phase = 'running' | 'waiting' | 'failed' | 'complete'

interface Props {
  target: TeamTarget | null
  team: string | null
  visible: boolean
  onDismiss: () => void
}

export default function TeamProvisionModal({
  target,
  team,
  visible,
  onDismiss,
}: Props) {
  const {t} = useTranslation()
  const queryClient = useQueryClient()
  const [steps, setSteps] = React.useState<LifecycleStep[]>([])
  const [phase, setPhase] = React.useState<Phase>('running')
  const [error, setError] = React.useState<string | null>(null)
  const [connectionLost, setConnectionLost] = React.useState(false)
  const [tick, setTick] = React.useState(0)

  React.useEffect(() => {
    if (!visible) return
    setSteps([])
    setError(null)
    setConnectionLost(false)
    setPhase('running')
  }, [visible, team])

  // Each call runs one step on the server; keep calling until done, failed or waiting for a step in progress.
  React.useEffect(() => {
    if (!visible || !team || !target || phase !== 'running') return
    let cancelled = false
    AdvanceLifecycleTeam(target, team)
      .then(result => {
        if (cancelled) return
        setSteps(result.steps)
        if (result.complete) {
          setPhase('complete')
          queryClient.invalidateQueries(TEAM_QUOTA_QUERY_KEY)
          notify(t('teamQuota.lifecycle.completed', {team}), 'success')
        } else if (result.steps.some(s => s.status === 'failed')) {
          setPhase('failed')
        } else if (result.steps.some(s => s.status === 'in_progress')) {
          setPhase('waiting')
        } else {
          setTick(value => value + 1)
        }
      })
      .catch(err => {
        if (cancelled) return
        if (isConnectionError(err)) setConnectionLost(true)
        else setError(errorMessage(err))
        setPhase('failed')
      })
    return () => {
      cancelled = true
    }
  }, [visible, team, target, phase, tick, queryClient, t])

  React.useEffect(() => {
    if (phase !== 'waiting') return
    const timer = setTimeout(() => setPhase('running'), IN_PROGRESS_WAIT_MS)
    return () => clearTimeout(timer)
  }, [phase])

  const retry = () => {
    setError(null)
    setConnectionLost(false)
    setPhase('running')
    setTick(value => value + 1)
  }

  const byId = Object.fromEntries(steps.map(step => [step.id, step]))
  const items = LIFECYCLE_STEPS.map(
    id => byId[id] || ({id, status: 'pending'} as LifecycleStep),
  )

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header={t('teamQuota.lifecycle.title', {team})}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            {phase === 'failed' && (
              <Button onClick={retry}>{t('teamQuota.lifecycle.retry')}</Button>
            )}
            <Button
              variant={phase === 'complete' ? 'primary' : 'normal'}
              onClick={onDismiss}
            >
              {t('teamQuota.actions.close')}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        <Box>{t('teamQuota.lifecycle.description')}</Box>
        {phase === 'waiting' && (
          <Alert type="info">{t('teamQuota.lifecycle.waiting')}</Alert>
        )}
        {phase === 'complete' && (
          <Alert type="success">
            {t('teamQuota.lifecycle.completed', {team})}
          </Alert>
        )}
        {connectionLost && (
          <Alert
            type="warning"
            header={t('teamQuota.lifecycle.connectionLostHeader')}
          >
            {t('teamQuota.lifecycle.connectionLost')}
          </Alert>
        )}
        {error && (
          <Alert type="error" header={t('teamQuota.lifecycle.failed')}>
            {error}
          </Alert>
        )}
        <Table
          variant="embedded"
          trackBy="id"
          items={items}
          columnDefinitions={[
            {
              id: 'step',
              header: t('teamQuota.lifecycle.step'),
              cell: item => t(`teamQuota.lifecycle.steps.${item.id}`),
            },
            {
              id: 'status',
              header: t('teamQuota.lifecycle.status'),
              cell: item => (
                <StatusIndicator type={INDICATOR[item.status]}>
                  {t(`teamQuota.lifecycle.statuses.${item.status}`)}
                </StatusIndicator>
              ),
            },
            {
              id: 'message',
              header: t('teamQuota.lifecycle.message'),
              cell: item => item.message || '',
            },
          ]}
        />
      </SpaceBetween>
    </Modal>
  )
}
