// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.

export type PeriodKind = 'thisMonth' | 'lastMonth' | 'last30Days' | 'custom'

export interface Period {
  start: string
  end: string // exclusive
}

export function toIsoDate(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate(),
  )}`
}

function addDays(date: Date, days: number): Date {
  const copy = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  copy.setDate(copy.getDate() + days)
  return copy
}

/** Return the period as [start, end) dates. A custom range includes its end date. */
export function computePeriod(
  kind: PeriodKind,
  today: Date = new Date(),
  custom?: {startDate: string; endDate: string} | null,
): Period | null {
  const firstOfMonth = new Date(today.getFullYear(), today.getMonth(), 1)
  const tomorrow = addDays(today, 1)
  switch (kind) {
    case 'thisMonth':
      return {start: toIsoDate(firstOfMonth), end: toIsoDate(tomorrow)}
    case 'lastMonth':
      return {
        start: toIsoDate(
          new Date(today.getFullYear(), today.getMonth() - 1, 1),
        ),
        end: toIsoDate(firstOfMonth),
      }
    case 'last30Days':
      return {start: toIsoDate(addDays(today, -29)), end: toIsoDate(tomorrow)}
    case 'custom': {
      const isoDate = /^\d{4}-\d{2}-\d{2}$/
      const start = custom?.startDate?.replace(/\//g, '-') ?? ''
      const last = custom?.endDate?.replace(/\//g, '-') ?? ''
      if (!isoDate.test(start) || !isoDate.test(last) || last < start)
        return null
      const [y, m, d] = last.split('-').map(Number)
      return {start, end: toIsoDate(addDays(new Date(y, m - 1, d), 1))}
    }
  }
}
