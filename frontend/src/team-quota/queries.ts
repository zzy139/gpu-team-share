// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {useMutation, useQuery, useQueryClient} from 'react-query'
import {
  AddTeamMember,
  AdvanceLifecycleTeam,
  CreateLifecycleTeam,
  CreateTeam,
  DeleteTeam,
  GetCapacity,
  GetCatalog,
  GetLeadQuota,
  GetTeamTrend,
  GetTeamUsage,
  ListAudit,
  ListLeadJobs,
  ListLifecycleTeams,
  ListTeamClusters,
  ListTeams,
  OffboardTeamMember,
  ReenableTeamMember,
  RemoveTeamMember,
  UpdateTeam,
} from './api'
import {
  AddMemberRequest,
  AuditFilters,
  CreateLifecycleTeamRequest,
  CreateTeamRequest,
  OffboardJobs,
  TeamTarget,
  UpdateTeamRequest,
} from './types'

export const TEAM_QUOTA_QUERY_KEY = 'TEAM_QUOTA'
const TEAMS_KEY = [TEAM_QUOTA_QUERY_KEY, 'teams']
const LIFECYCLE_KEY = [TEAM_QUOTA_QUERY_KEY, 'lifecycle']
const LEAD_REFRESH_MS = 30000
// Usage and trend queries hit Cost Explorer, which is billed per request.
const COST_STALE_TIME = 5 * 60 * 1000

function targetKey(target: TeamTarget | null) {
  return target ? [target.clusterName, target.region || ''] : []
}

export function useTeamClustersQuery(region?: string) {
  return useQuery([TEAM_QUOTA_QUERY_KEY, 'clusters', region || ''], () =>
    ListTeamClusters(region),
  )
}

export function useTeamsQuery(target: TeamTarget | null) {
  return useQuery(
    [...TEAMS_KEY, ...targetKey(target)],
    () => ListTeams(target!),
    {
      enabled: !!target,
    },
  )
}

/** Admin only: the lifecycle records. Leads must never call it (the server answers 403). */
export function useLifecycleTeamsQuery(
  target: TeamTarget | null,
  enabled: boolean,
) {
  return useQuery(
    [...LIFECYCLE_KEY, ...targetKey(target)],
    () => ListLifecycleTeams(target!),
    {enabled: !!target && enabled},
  )
}

/** `team` limits the usage to one team (the cost tab of the team page). */
export function useTeamUsageQuery(
  target: TeamTarget | null,
  start: string,
  end: string,
  team?: string,
) {
  return useQuery(
    [TEAM_QUOTA_QUERY_KEY, 'usage', ...targetKey(target), start, end, team],
    () => GetTeamUsage(target!, start, end, team),
    {enabled: !!target && !!start && !!end, staleTime: COST_STALE_TIME},
  )
}

export function useTeamTrendQuery(
  target: TeamTarget | null,
  months: number,
  team?: string,
) {
  return useQuery(
    [TEAM_QUOTA_QUERY_KEY, 'trend', ...targetKey(target), months, team],
    () => GetTeamTrend(target!, months, team),
    {enabled: !!target, staleTime: COST_STALE_TIME},
  )
}

export function useCapacityQuery(target: TeamTarget | null, team?: string) {
  return useQuery(
    [TEAM_QUOTA_QUERY_KEY, 'capacity', ...targetKey(target), team],
    () => GetCapacity(target!, team),
    {enabled: !!target, refetchInterval: LEAD_REFRESH_MS},
  )
}

export function useCatalogQuery(target: TeamTarget | null) {
  return useQuery(
    [TEAM_QUOTA_QUERY_KEY, 'catalog', ...targetKey(target)],
    () => GetCatalog(target!),
    {enabled: !!target, staleTime: 5 * 60 * 1000},
  )
}
export function useAuditQuery(filters: AuditFilters, enabled = true) {
  return useQuery(
    [TEAM_QUOTA_QUERY_KEY, 'audit', JSON.stringify(filters)],
    () => ListAudit(filters),
    {enabled},
  )
}
export function useLeadJobsQuery(target: TeamTarget | null, team: string) {
  return useQuery(
    [TEAM_QUOTA_QUERY_KEY, 'leadJobs', ...targetKey(target), team],
    () => ListLeadJobs(target!, team),
    {enabled: !!target && !!team, refetchInterval: LEAD_REFRESH_MS},
  )
}
export function useLeadQuotaQuery(target: TeamTarget | null, team: string) {
  return useQuery(
    [TEAM_QUOTA_QUERY_KEY, 'leadQuota', ...targetKey(target), team],
    () => GetLeadQuota(target!, team),
    {enabled: !!target && !!team, refetchInterval: LEAD_REFRESH_MS},
  )
}
function useTeamMutation<TVariables, TResult>(
  mutationFn: (variables: TVariables) => Promise<TResult>,
) {
  const queryClient = useQueryClient()
  return useMutation(mutationFn, {
    onSettled: () => {
      queryClient.invalidateQueries(TEAMS_KEY)
      queryClient.invalidateQueries(LIFECYCLE_KEY)
    },
  })
}

export function useCreateTeamMutation(target: TeamTarget | null) {
  return useTeamMutation((body: CreateTeamRequest) => CreateTeam(target!, body))
}

export function useUpdateTeamMutation(target: TeamTarget | null) {
  return useTeamMutation(
    ({team, body}: {team: string; body: UpdateTeamRequest}) =>
      UpdateTeam(target!, team, body),
  )
}

export function useDeleteTeamMutation(target: TeamTarget | null) {
  return useTeamMutation((team: string) => DeleteTeam(target!, team))
}

export function useAddTeamMemberMutation(target: TeamTarget | null) {
  return useTeamMutation(
    ({
      team,
      member,
    }: {
      team: string
      member: AddMemberRequest | {user: string}
    }) => AddTeamMember(target!, team, member),
  )
}

export function useCreateLifecycleTeamMutation(target: TeamTarget | null) {
  return useTeamMutation((body: CreateLifecycleTeamRequest) =>
    CreateLifecycleTeam(target!, body),
  )
}

export function useAdvanceLifecycleTeamMutation(target: TeamTarget | null) {
  return useTeamMutation((team: string) => AdvanceLifecycleTeam(target!, team))
}

export function useRemoveTeamMemberMutation(target: TeamTarget | null) {
  return useTeamMutation(({team, user}: {team: string; user: string}) =>
    RemoveTeamMember(target!, team, user),
  )
}
export function useOffboardMemberMutation(target: TeamTarget | null) {
  return useTeamMutation(
    ({team, user, jobs}: {team: string; user: string; jobs: OffboardJobs}) =>
      OffboardTeamMember(target!, team, user, jobs),
  )
}
export function useReenableMemberMutation(target: TeamTarget | null) {
  return useTeamMutation(({team, user}: {team: string; user: string}) =>
    ReenableTeamMember(target!, team, user),
  )
}
