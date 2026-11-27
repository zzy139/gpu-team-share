// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {Badge, Box, SpaceBetween} from '@cloudscape-design/components'
import React from 'react'
import {TFunction, useTranslation} from 'react-i18next'
import {JobAlert} from './types'

const COLOR: Record<JobAlert, 'red' | 'blue' | 'grey'> = {
  idle: 'red',
  long_run: 'blue',
  no_capacity: 'grey',
}

/** 疑似空闲 / 已运行超24小时 / 暂无容量 badges of a job; nothing when there are no alerts. */
export function JobAlertBadges({alerts}: {alerts: JobAlert[] | undefined}) {
  const {t} = useTranslation()
  const known = (alerts || []).filter(alert => alert in COLOR)
  if (!known.length) return null
  return (
    <SpaceBetween direction="horizontal" size="xxs">
      {known.map(alert => (
        <Badge key={alert} color={COLOR[alert]}>
          {t(`teamQuota.member.alerts.${alert}`)}
        </Badge>
      ))}
    </SpaceBetween>
  )
}

function pad(value: number) {
  return String(value).padStart(2, '0')
}

/** Local wall-clock time hh:mm:ss of a timestamp in milliseconds. */
export function clockTime(timestamp: number) {
  const date = new Date(timestamp)
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(
    date.getSeconds(),
  )}`
}

/** "最后更新：hh:mm:ss" of a react-query result (dataUpdatedAt); nothing before the first answer. */
export function LastUpdated({updatedAt}: {updatedAt: number | undefined}) {
  const {t} = useTranslation()
  if (!updatedAt) return null
  return (
    <Box variant="small" color="text-body-secondary">
      {t('teamQuota.member.lastUpdated', {time: clockTime(updatedAt)})}
    </Box>
  )
}

/** True when a Slurm time limit is UNLIMITED or not set. */
export function isUnlimited(value: string | null | undefined) {
  return !value || /^(UNLIMITED|INFINITE|NONE)$/i.test(value.trim())
}

/** Slurm time limit; UNLIMITED (or none) is shown as 不限. */
export function timeLimitText(t: TFunction, value: string | null | undefined) {
  return isUnlimited(value) ? t('teamQuota.member.unlimited') : value!
}
