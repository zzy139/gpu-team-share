// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {UserIdentity} from '../auth/types'
import {isValidTeamName} from './validation'

export const TEAM_GROUP_PREFIX = 'team-'

type MaybeIdentity = Pick<UserIdentity, 'user_roles'> | null | undefined

function roles(identity: MaybeIdentity): string[] {
  return Array.isArray(identity?.user_roles) ? identity!.user_roles : []
}

export function isAdmin(identity: MaybeIdentity): boolean {
  return roles(identity).includes('admin')
}

/** Team names from the caller's `team-<team>` groups. The server enforces the same rule. */
export function teamsFromRoles(identity: MaybeIdentity): string[] {
  const teams = roles(identity)
    .filter(role => role.startsWith(TEAM_GROUP_PREFIX))
    .map(role => role.slice(TEAM_GROUP_PREFIX.length))
    .filter(isValidTeamName)
  return Array.from(new Set(teams)).sort()
}

export function isTeamLeadOnly(identity: MaybeIdentity): boolean {
  return !isAdmin(identity) && teamsFromRoles(identity).length > 0
}

/** `member-<team>` groups: business users of the self-service portal (not `team-<team>-member`, see README). */
export const MEMBER_GROUP_PREFIX = 'member-'

/** Team names from the caller's `member-<team>` groups. The server enforces the same rule. */
export function memberTeamsFromRoles(identity: MaybeIdentity): string[] {
  const teams = roles(identity)
    .filter(role => role.startsWith(MEMBER_GROUP_PREFIX))
    .map(role => role.slice(MEMBER_GROUP_PREFIX.length))
    .filter(isValidTeamName)
  return Array.from(new Set(teams)).sort()
}

export function hasMemberRole(identity: MaybeIdentity): boolean {
  return memberTeamsFromRoles(identity).length > 0
}

/** Only `member-<team>` groups: the member router with the portal pages only. */
export function isMemberOnly(identity: MaybeIdentity): boolean {
  return (
    !isAdmin(identity) &&
    teamsFromRoles(identity).length === 0 &&
    hasMemberRole(identity)
  )
}
