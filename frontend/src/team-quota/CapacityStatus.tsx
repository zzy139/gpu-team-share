// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {
  StatusIndicator,
  StatusIndicatorProps,
} from '@cloudscape-design/components'
import React from 'react'
import {TFunction, useTranslation} from 'react-i18next'
import {CapacityStatus} from './types'

const INDICATOR: Record<CapacityStatus, StatusIndicatorProps.Type> = {
  available: 'success',
  startable: 'pending',
  no_capacity: 'warning',
  busy: 'in-progress',
}

/** 有空闲 / 需启动约5分钟 / 暂无容量 / 全部在用，需排队 of one instance type; '-' when unknown. */
export function CapacityStatusBadge({
  status,
}: {
  status: CapacityStatus | null | undefined
}) {
  const {t} = useTranslation()
  if (!status || !(status in INDICATOR)) return <>-</>
  return (
    <StatusIndicator type={INDICATOR[status]}>
      {t(`teamQuota.capacity.status.${status}`)}
    </StatusIndicator>
  )
}

const NODE_STATES = [
  'idle',
  'alloc',
  'mix',
  'comp',
  'down',
  'drain',
  'drng',
  'fail',
  'maint',
  'resv',
] as const
type NodeState = typeof NODE_STATES[number]
type NodeFlag = 'poweredOff' | 'poweringUp' | 'poweringDown' | 'notResponding'
const NODE_FLAGS: Record<string, NodeFlag> = {
  '~': 'poweredOff',
  '#': 'poweringUp',
  '%': 'poweringDown',
  '!': 'poweringDown',
  '*': 'notResponding',
}

/** Chinese meaning of a sinfo node state such as 'idle~' or 'down*'; the raw state is shown next to it. */
export function nodeStateText(t: TFunction, state: string): string {
  const match = /^([a-z]+)([~#%!*]*)$/i.exec(state.trim())
  if (!match) return state
  const base = match[1].toLowerCase()
  const known = (NODE_STATES as readonly string[]).includes(base)
  const words = [
    known ? t(`teamQuota.capacity.nodeStates.${base as NodeState}`) : base,
    ...Array.from(new Set(match[2].split('').filter(flag => flag))).map(flag =>
      t(`teamQuota.capacity.nodeFlags.${NODE_FLAGS[flag]}`),
    ),
  ]
  return t('teamQuota.capacity.nodeState', {state, meaning: words.join(' / ')})
}
