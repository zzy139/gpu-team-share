// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.

// Mirrors api/teams/validation.py. The server re-validates every value.
export const NAME_RE = /^[a-z][a-z0-9_-]{0,31}$/
// Lifecycle teams become AD group and ECR repository names: lowercase, no '_'.
export const LIFECYCLE_NAME_RE = /^[a-z][a-z0-9-]{0,31}$/
// The cluster (AD / Linux / Slurm) user is the local part of the email (sAMAccountName <= 20).
export const USER_PREFIX_RE = /^[a-z][a-z0-9._-]{0,19}$/
export const EMAIL_RE = /^[a-z0-9._-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)+$/
export const EMAIL_MAX_LENGTH = 128
export const MAX_LIFECYCLE_MEMBERS = 10
export const RESERVED_NAMES = [
  'root',
  'pcdefault',
  'normal',
  'borrow',
  'envbuild',
  'shared',
]
export const RESERVED_USERS = ['root', 'slurm']
export const FAIRSHARE = {min: 1, max: 10000}
/** Machines of one instance type a team may run at the same time (0 = not allowed). */
export const TYPE_LIMIT = {min: 0, max: 1000}
export const BUDGET_USD = {min: 0, max: 10000000}
export const ENV_CAP_GB = {min: 0, max: 100000}

export type ValidationErrorKey =
  | 'teamQuota.errors.invalidName'
  | 'teamQuota.errors.reservedName'
  | 'teamQuota.errors.invalidNumber'
  | 'teamQuota.errors.outOfRange'
  | 'teamQuota.errors.invalidLifecycleName'
  | 'teamQuota.errors.invalidEmail'
  | 'teamQuota.errors.invalidEmailUser'
  | 'teamQuota.errors.emailDomain'
  | 'teamQuota.errors.duplicateEmail'
  | 'teamQuota.errors.emailMissingAt'
  | 'teamQuota.errors.noTypeAllowed'
  | 'teamQuota.errors.invalidAmount'

export function isValidTeamName(name: string): boolean {
  return NAME_RE.test(name) && !RESERVED_NAMES.includes(name)
}

export function validateTeamName(name: string): ValidationErrorKey | null {
  if (!NAME_RE.test(name)) return 'teamQuota.errors.invalidName'
  if (RESERVED_NAMES.includes(name)) return 'teamQuota.errors.reservedName'
  return null
}

export function validateLifecycleTeamName(
  name: string,
): ValidationErrorKey | null {
  const base = validateTeamName(name)
  if (base) return base
  if (!LIFECYCLE_NAME_RE.test(name))
    return 'teamQuota.errors.invalidLifecycleName'
  return null
}

/** The cluster user name derived from an email (its lowercased local part), or null. */
export function userFromEmail(email: string): string | null {
  const normalized = (email ?? '').trim().toLowerCase()
  if (normalized.length > EMAIL_MAX_LENGTH || !EMAIL_RE.test(normalized))
    return null
  const user = normalized.split('@')[0]
  return USER_PREFIX_RE.test(user) && !RESERVED_USERS.includes(user)
    ? user
    : null
}

export function validateEmail(
  email: string,
  domains: string[] = [],
): ValidationErrorKey | null {
  const normalized = (email ?? '').trim().toLowerCase()
  if (normalized && !normalized.includes('@'))
    return 'teamQuota.errors.emailMissingAt'
  if (normalized.length > EMAIL_MAX_LENGTH || !EMAIL_RE.test(normalized))
    return 'teamQuota.errors.invalidEmail'
  if (!userFromEmail(normalized)) return 'teamQuota.errors.invalidEmailUser'
  const domain = normalized.split('@')[1]
  if (domains.length && !domains.includes(domain))
    return 'teamQuota.errors.emailDomain'
  return null
}

export function validateUserName(name: string): ValidationErrorKey | null {
  if (!USER_PREFIX_RE.test(name)) return 'teamQuota.errors.invalidName'
  if (RESERVED_USERS.includes(name)) return 'teamQuota.errors.reservedName'
  return null
}

export function parseInteger(value: string): number | null {
  const trimmed = (value ?? '').trim()
  return /^-?\d{1,9}$/.test(trimmed) ? Number(trimmed) : null
}

export function validateInteger(
  value: string,
  range: {min: number; max: number},
): ValidationErrorKey | null {
  const parsed = parseInteger(value)
  if (parsed === null) return 'teamQuota.errors.invalidNumber'
  if (parsed < range.min || parsed > range.max)
    return 'teamQuota.errors.outOfRange'
  return null
}
/** A non-negative amount with at most two decimals (USD), or null. */
export function parseAmount(value: string): number | null {
  const trimmed = (value ?? '').trim()
  return /^\d{1,9}(\.\d{1,2})?$/.test(trimmed) ? Number(trimmed) : null
}
/** Empty = no value; otherwise an amount within ``range``. */
export function validateOptionalAmount(
  value: string,
  range: {min: number; max: number},
): ValidationErrorKey | null {
  if (!(value ?? '').trim()) return null
  const parsed = parseAmount(value)
  if (parsed === null) return 'teamQuota.errors.invalidAmount'
  if (parsed < range.min || parsed > range.max)
    return 'teamQuota.errors.outOfRange'
  return null
}
/** Empty = no value; otherwise a whole number within ``range``. */
export function validateOptionalInteger(
  value: string,
  range: {min: number; max: number},
): ValidationErrorKey | null {
  return (value ?? '').trim() ? validateInteger(value, range) : null
}
