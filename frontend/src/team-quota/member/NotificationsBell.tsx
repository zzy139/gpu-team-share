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
  Badge,
  Box,
  Button,
  Modal,
  SpaceBetween,
  StatusIndicator,
  Table,
  Toggle,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {notify} from '../../model'
import {errorMessage} from '../api'
import {stateLabel} from './format'
import {
  useMarkReadMutation,
  useNotificationsQuery,
  usePreferencesQuery,
  useUpdatePreferencesMutation,
} from './queries'
import {MemberTarget, PortalNotification, Preferences} from './types'

const ALERT_KINDS = ['gpu_idle', 'long_run', 'no_capacity'] as const
type AlertKind = typeof ALERT_KINDS[number]

function NotificationText({item}: {item: PortalNotification}) {
  const {t} = useTranslation()
  if (item.kind === 'budget')
    return <>{t('teamQuota.member.bell.budget', {team: item.team || '-'})}</>
  if ((ALERT_KINDS as readonly string[]).includes(item.kind)) {
    const kind = item.kind as AlertKind
    const values = {
      jobId: item.job_id,
      name: item.name || '-',
      type: item.instance_type || item.partition || '-',
      hours: item.hours ?? '-',
      minutes: item.minutes ?? '-',
    }
    return (
      <SpaceBetween size="xxs">
        <Box>
          <Badge color={kind === 'gpu_idle' ? 'red' : 'blue'}>
            {t(`teamQuota.member.bell.kinds.${kind}`)}
          </Badge>
        </Box>
        {/* the server's Chinese text names the alternatives; the keyed text is the fallback */}
        <Box>
          {item.text || t(`teamQuota.member.bell.alerts.${kind}`, values)}
        </Box>
      </SpaceBetween>
    )
  }
  return (
    <>
      {t('teamQuota.member.bell.job', {
        jobId: item.job_id,
        name: item.name || '-',
        state: stateLabel(t, item.state),
        type: item.instance_type || item.partition || '-',
      })}
    </>
  )
}

export function PreferencesModal({
  target,
  visible,
  onDismiss,
}: {
  target: MemberTarget | null
  visible: boolean
  onDismiss: () => void
}) {
  const {t} = useTranslation()
  const prefs = usePreferencesQuery(target, visible)
  const mutation = useUpdatePreferencesMutation(target)
  const toggle = (key: keyof Preferences, value: boolean) =>
    mutation.mutate(
      {[key]: value},
      {onError: err => notify(errorMessage(err), 'error')},
    )
  const data = prefs.data
  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header={t('teamQuota.member.prefs.title')}
      footer={
        <Box float="right">
          <Button onClick={onDismiss}>{t('teamQuota.actions.close')}</Button>
        </Box>
      }
    >
      <SpaceBetween size="m">
        {prefs.isError && (
          <Alert type="error">{errorMessage(prefs.error)}</Alert>
        )}
        {(
          ['job_notify', 'email_jobs', 'email_budget', 'email_alerts'] as const
        ).map(key => (
          <Toggle
            key={key}
            checked={!!data?.[key]}
            disabled={!data || mutation.isLoading}
            onChange={({detail}) => toggle(key, detail.checked)}
            description={
              key === 'email_alerts'
                ? t('teamQuota.member.prefs.emailAlertsDescription')
                : undefined
            }
          >
            {t(`teamQuota.member.prefs.${key}`)}
          </Toggle>
        ))}
        {data && (
          <Box variant="small">
            {t(`teamQuota.member.prefs.email.${data.email.status}`, {
              address: data.email.address,
            })}
          </Box>
        )}
        {data && data.email.status !== 'confirmed' && (
          <Alert type="info">
            {t('teamQuota.member.prefs.confirmHint', {
              address: data.email.address,
            })}
          </Alert>
        )}
      </SpaceBetween>
    </Modal>
  )
}

/** Bell with the unread count; opens the job-end, budget and job-alert notifications. */
export default function NotificationsBell({
  target,
}: {
  target: MemberTarget | null
}) {
  const {t} = useTranslation()
  const {data} = useNotificationsQuery(target)
  const markRead = useMarkReadMutation(target)
  const [open, setOpen] = React.useState(false)
  const [prefsOpen, setPrefsOpen] = React.useState(false)
  const unread = data?.unread ?? 0
  return (
    <>
      <SpaceBetween direction="horizontal" size="xs">
        <Button
          iconName="notification"
          ariaLabel={t('teamQuota.member.bell.aria', {count: unread})}
          onClick={() => setOpen(true)}
        >
          {unread > 0
            ? t('teamQuota.member.bell.unread', {count: unread})
            : t('teamQuota.member.bell.title')}
        </Button>
        <Button iconName="settings" onClick={() => setPrefsOpen(true)}>
          {t('teamQuota.member.prefs.open')}
        </Button>
      </SpaceBetween>
      <Modal
        visible={open}
        size="large"
        onDismiss={() => setOpen(false)}
        header={t('teamQuota.member.bell.title')}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                disabled={!unread}
                loading={markRead.isLoading}
                onClick={() => markRead.mutate()}
              >
                {t('teamQuota.member.bell.markRead')}
              </Button>
              <Button onClick={() => setOpen(false)}>
                {t('teamQuota.actions.close')}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Table<PortalNotification>
          variant="embedded"
          trackBy="id"
          items={data?.items || []}
          empty={
            <Box textAlign="center">{t('teamQuota.member.bell.empty')}</Box>
          }
          columnDefinitions={[
            {
              id: 'unread',
              header: '',
              cell: item =>
                item.unread ? (
                  <StatusIndicator type="info">
                    {t('teamQuota.member.bell.new')}
                  </StatusIndicator>
                ) : null,
            },
            {
              id: 'text',
              header: t('teamQuota.member.bell.message'),
              cell: item => <NotificationText item={item} />,
            },
            {
              id: 'end',
              header: t('teamQuota.member.columns.end'),
              cell: item => item.end || '-',
            },
          ]}
        />
      </Modal>
      <PreferencesModal
        target={target}
        visible={prefsOpen}
        onDismiss={() => setPrefsOpen(false)}
      />
    </>
  )
}
