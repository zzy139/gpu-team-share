// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {TFunction} from 'react-i18next'

const KNOWN_STATES = [
  'PENDING',
  'RUNNING',
  'COMPLETED',
  'FAILED',
  'CANCELLED',
  'TIMEOUT',
  'CONFIGURING',
  'COMPLETING',
  'NODE_FAIL',
  'OUT_OF_MEMORY',
  'PREEMPTED',
  'SUSPENDED',
] as const
type KnownState = typeof KNOWN_STATES[number]

/** Chinese label of a Slurm job state; sacct may append details such as "CANCELLED by 1001". */
export function stateLabel(t: TFunction, state: string | null | undefined) {
  if (!state) return '-'
  const base = state.split(' ')[0]
  return (KNOWN_STATES as readonly string[]).includes(base)
    ? t(`teamQuota.member.states.${base as KnownState}`)
    : state
}
