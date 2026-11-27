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
  FormField,
  Input,
  Table,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {formatSpec} from './format'
import {CatalogType, TypeLimits, UnmanagedQueue} from './types'
import {
  parseInteger,
  TYPE_LIMIT,
  validateOptionalInteger,
  ValidationErrorKey,
} from './validation'

/** Queues with several instance types: per-type quotas cannot count them (plan R1). */
export function multiTypeQueues(queues: UnmanagedQueue[] | undefined) {
  return (queues || []).filter(queue => queue.reason === 'multi_type')
}

export function MultiTypeQueuesAlert({
  queues,
}: {
  queues: UnmanagedQueue[] | undefined
}) {
  const {t} = useTranslation()
  const multi = multiTypeQueues(queues)
  if (!multi.length) return null
  return (
    <Alert type="warning" header={t('teamQuota.multiType.header')}>
      <ul>
        {multi.map(queue => (
          <li key={queue.queue}>
            {t('teamQuota.multiType.body', {
              queue: queue.queue,
              types: queue.instance_types.join(', '),
            })}
          </li>
        ))}
      </ul>
    </Alert>
  )
}

export type TypeLimitValues = Record<string, string>

export function initialTypeValues(
  catalog: CatalogType[],
  limits?: TypeLimits,
): TypeLimitValues {
  return Object.fromEntries(
    catalog.map(entry => {
      const value = limits?.[entry.instance_type]
      return [entry.instance_type, value ? String(value) : '']
    }),
  )
}

/** Per-type errors; empty and 0 mean "not allowed". */
export function validateTypeValues(
  catalog: CatalogType[],
  values: TypeLimitValues,
): Record<string, ValidationErrorKey | null> {
  return Object.fromEntries(
    catalog.map(entry => [
      entry.instance_type,
      validateOptionalInteger(values[entry.instance_type] ?? '', TYPE_LIMIT),
    ]),
  )
}

/** {instance_type: machines} of every catalog type (empty = 0). */
export function typeLimitsFrom(
  catalog: CatalogType[],
  values: TypeLimitValues,
): TypeLimits {
  return Object.fromEntries(
    catalog.map(entry => [
      entry.instance_type,
      parseInteger(values[entry.instance_type] ?? '') ?? 0,
    ]),
  )
}

export function TypeLimitsTable({
  catalog,
  values,
  errors,
  onChange,
}: {
  catalog: CatalogType[]
  values: TypeLimitValues
  errors: Record<string, ValidationErrorKey | null | undefined>
  onChange: (instanceType: string, value: string) => void
}) {
  const {t} = useTranslation()
  return (
    <Table
      variant="embedded"
      trackBy="instance_type"
      items={catalog}
      empty={<Box textAlign="center">{t('teamQuota.typeLimits.empty')}</Box>}
      columnDefinitions={[
        {
          id: 'type',
          header: t('teamQuota.typeLimits.instanceType'),
          cell: entry => entry.instance_type,
        },
        {
          id: 'queue',
          header: t('teamQuota.typeLimits.queue'),
          cell: entry => entry.partition,
        },
        {
          id: 'spec',
          header: t('teamQuota.typeLimits.spec'),
          cell: entry => formatSpec(entry),
        },
        {
          id: 'machines',
          header: t('teamQuota.typeLimits.machines'),
          cell: entry => {
            const error = errors[entry.instance_type]
            return (
              <FormField errorText={error && t(error, TYPE_LIMIT)}>
                <Input
                  type="number"
                  ariaLabel={t('teamQuota.typeLimits.machinesAria', {
                    type: entry.instance_type,
                  })}
                  placeholder="0"
                  value={values[entry.instance_type] ?? ''}
                  onChange={({detail}) =>
                    onChange(entry.instance_type, detail.value)
                  }
                />
              </FormField>
            )
          },
        },
      ]}
    />
  )
}
