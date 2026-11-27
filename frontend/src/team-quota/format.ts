// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {CatalogType, Team} from './types'

export type SpecSource = Pick<CatalogType, 'gpus' | 'vcpus' | 'memory_gib'> &
  Partial<Pick<CatalogType, 'gpu_model' | 'gpu_memory_gb'>>

/** Machine specs of an instance type, e.g. '1× NVIDIA L40S 48 GB · 4 vCPU · 32 GiB'; '-' when nothing is known. */
export function formatSpec(entry: SpecSource | null | undefined): string {
  if (!entry) return '-'
  const parts: string[] = []
  if (entry.gpus) {
    const memory = entry.gpu_memory_gb ? ` ${entry.gpu_memory_gb} GB` : ''
    parts.push(`${entry.gpus}× ${entry.gpu_model || 'GPU'}${memory}`)
  }
  if (entry.vcpus) parts.push(`${entry.vcpus} vCPU`)
  if (entry.memory_gib) parts.push(`${entry.memory_gib} GiB`)
  return parts.length ? parts.join(' · ') : '-'
}

export function formatNumber(value: number | null | undefined, digits = 2) {
  return value === null || value === undefined ? '-' : value.toFixed(digits)
}

export function formatPercent(value: number | null | undefined, digits = 1) {
  return value === null || value === undefined
    ? '-'
    : `${(value * 100).toFixed(digits)}%`
}

export function formatDollars(value: number | null | undefined) {
  if (value === null || value === undefined) return '-'
  const digits = Math.abs(value) < 1 && value !== 0 ? 4 : 2
  return `$${value.toFixed(digits)}`
}

/** Allowed instance types of a team as 'g6e.xlarge × 2, g6e.2xlarge × 1' (types with 0 are not allowed). */
export function typeLimitsText(team: Team): string {
  return Object.entries(team.type_limits || {})
    .filter(([, machines]) => machines > 0)
    .map(([type, machines]) => `${type} × ${machines}`)
    .join(', ')
}
/** Machines in use (running + borrowed) of one instance type. */
export function machinesInUse(team: Team, instanceType: string): number {
  const usage = team.running_by_type?.[instanceType]
  return usage ? usage.running + usage.borrowed : 0
}
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000
    unit += 1
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`
}
