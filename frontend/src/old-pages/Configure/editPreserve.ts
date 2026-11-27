// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.

// Editing an existing cluster must only send what the administrator changed. The wizard pages fill in their own
// defaults (key pair, gp3 volume types, placement groups, SSH key generation, ...) and the loaded configuration of
// clusters created through CloudFormation stores numbers and booleans as strings. ParallelCluster compares the
// submitted configuration with the deployed one, so each of these differences becomes a change, some of which are not
// updatable or need the compute fleet stopped. preserveUnchanged() keeps the loaded value wherever the wizard value is
// equivalent, and drops keys the wizard added to existing sections when they only hold such defaults.

type Json = any

const isObject = (value: Json) =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

/** '10' and 10, 'true' and true compare equal. */
export function sameScalar(a: Json, b: Json): boolean {
  if (a === b) return true
  if (a === null || b === null || a === undefined || b === undefined)
    return false
  if (typeof a === 'object' || typeof b === 'object') return false
  return String(a).toLowerCase() === String(b).toLowerCase()
}

export function equivalent(a: Json, b: Json): boolean {
  if (Array.isArray(a) && Array.isArray(b))
    return a.length === b.length && a.every((x, i) => equivalent(x, b[i]))
  if (isObject(a) && isObject(b)) {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)])
    return Array.from(keys).every(k => equivalent(a[k], b[k]))
  }
  return sameScalar(a, b)
}

// Values the wizard sets on its own. A key that was absent from the loaded configuration and only holds one of these
// is not sent. Any other added value (something the administrator typed or chose) is kept.
const DEFAULT_VALUES: Record<string, (value: Json) => boolean> = {
  KeyName: () => true, // the wizard picks the first key pair; not updatable on a running cluster
  Region: () => true,
  VolumeType: v => sameScalar(v, 'gp3'),
  Enabled: v => sameScalar(v, false),
  GenerateSshKeysForUsers: v => sameScalar(v, true),
  AllocationStrategy: v => sameScalar(v, 'lowest-price'),
  CapacityType: v => sameScalar(v, 'ONDEMAND'),
  DisableSimultaneousMultithreading: v => sameScalar(v, false),
  Encrypted: v => sameScalar(v, true),
  DeleteOnTermination: v => sameScalar(v, true),
}

/** True when ``value`` under ``key`` holds nothing but wizard defaults (empty objects included). */
export function onlyDefaults(key: string, value: Json): boolean {
  if (value === undefined || value === null) return true
  if (isObject(value))
    return Object.entries(value).every(([k, v]) => onlyDefaults(k, v))
  if (Array.isArray(value)) return value.length === 0
  const check = DEFAULT_VALUES[key]
  return check ? check(value) : false
}

const named = (items: Json[]) =>
  items.length > 0 && items.every(item => isObject(item) && item.Name)

export function preserveUnchanged(loaded: Json, current: Json): Json {
  if (current === undefined) return undefined
  if (Array.isArray(loaded) && Array.isArray(current)) {
    if (named(loaded) && named(current)) {
      const byName = new Map(loaded.map((item: Json) => [item.Name, item]))
      return current.map((item: Json) =>
        byName.has(item.Name)
          ? preserveUnchanged(byName.get(item.Name), item)
          : item,
      )
    }
    return equivalent(loaded, current) ? loaded : current
  }
  if (isObject(loaded) && isObject(current)) {
    const out: Record<string, Json> = {}
    for (const [key, value] of Object.entries(current)) {
      if (key in loaded) {
        const kept = preserveUnchanged(loaded[key], value)
        if (kept !== undefined) out[key] = kept
      } else if (!onlyDefaults(key, value)) {
        out[key] = value
      }
    }
    return out
  }
  return sameScalar(loaded, current) ? loaded : current
}

/** Subnets for a new queue: the subnets most existing queues use (the private compute subnets). */
export function defaultQueueSubnets(
  queues: Json[],
  fallback?: string,
): string[] {
  const counts = new Map<string, {subnets: string[]; n: number}>()
  for (const queue of queues || []) {
    const subnets: string[] = queue?.Networking?.SubnetIds || []
    if (!subnets.length) continue
    const key = [...subnets].sort().join(',')
    const entry = counts.get(key) || {subnets, n: 0}
    entry.n += 1
    counts.set(key, entry)
  }
  let best: {subnets: string[]; n: number} | undefined
  counts.forEach(entry => {
    if (!best || entry.n > best.n) best = entry
  })
  if (best) return [...best.subnets]
  return fallback ? [fallback] : []
}

/** Node setup for a new queue: the CustomActions and Iam that most existing queues use. Without them a new queue's
 * nodes skip the cluster's node script (Enroot/Pyxis setup, GPU container support) and cannot read it from S3. */
export function defaultQueueNodeSetup(queues: Json[]): Json {
  const counts = new Map<string, {setup: Json; n: number}>()
  for (const queue of queues || []) {
    const setup: Json = {}
    if (isObject(queue?.CustomActions))
      setup.CustomActions = queue.CustomActions
    if (isObject(queue?.Iam)) setup.Iam = queue.Iam
    if (!Object.keys(setup).length) continue
    const key = JSON.stringify(setup)
    const entry = counts.get(key) || {setup, n: 0}
    entry.n += 1
    counts.set(key, entry)
  }
  let best: {setup: Json; n: number} | undefined
  counts.forEach(entry => {
    if (!best || entry.n > best.n) best = entry
  })
  return best ? JSON.parse(JSON.stringify(best.setup)) : {}
}

/** Instance types of a queue over all its compute resources. */
export function queueInstanceTypes(queue: Json): string[] {
  const types = new Set<string>()
  for (const cr of queue?.ComputeResources || []) {
    if (cr.InstanceType) types.add(cr.InstanceType)
    for (const instance of cr.Instances || [])
      if (instance?.InstanceType) types.add(instance.InstanceType)
  }
  return Array.from(types)
}
