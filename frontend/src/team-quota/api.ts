// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {AxiosError} from 'axios'
import {handleNotAuthorizedErrors} from '../auth/handleNotAuthorizedErrors'
import {executeRequest, HTTPMethod} from '../http/executeRequest'
import {getState} from '../store'
import {
  AddMemberRequest,
  AddMemberResponse,
  AuditFilters,
  AuditResponse,
  CapacityResponse,
  CatalogResponse,
  CreateLifecycleTeamRequest,
  CreateTeamRequest,
  DeleteTeamResponse,
  LeadJobsResponse,
  LeadQuotaResponse,
  LifecycleListResponse,
  LifecycleTeamResponse,
  OffboardJobs,
  OffboardResponse,
  TeamClustersResponse,
  TeamListResponse,
  TeamTarget,
  TeamTrendResponse,
  TeamUsageResponse,
  TeamWriteResponse,
  UpdateTeamRequest,
} from './types'

const BASE = 'team-quota'

type Params = Record<string, string | number | undefined | null>

export function toQuery(params: Params): string {
  return Object.entries(params)
    .filter(
      ([, value]) => value !== undefined && value !== null && value !== '',
    )
    .map(
      ([key, value]) =>
        `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`,
    )
    .join('&')
}

function selectedRegion(): string | undefined {
  return getState(['app', 'selectedRegion']) || undefined
}

function targetParams(target: TeamTarget): Params {
  return {
    cluster_name: target.clusterName,
    region: target.region || selectedRegion(),
  }
}

/**
 * Only a 401 (expired or missing session) sends the user back to the login page.
 * A 403 is a real authorization answer from the server and is shown as an error,
 * so a team lead never ends up in a redirect loop.
 */
export async function teamRequest<T>(
  method: HTTPMethod,
  url: string,
  body?: unknown,
): Promise<T> {
  try {
    const {data} = await executeRequest(method, url, body, {})
    return data as T
  } catch (error) {
    const appConfig = getState(['app', 'appConfig'])
    if ((error as AxiosError)?.response?.status === 401 && appConfig) {
      await handleNotAuthorizedErrors(appConfig)(Promise.reject(error))
    }
    throw error
  }
}

function teamPath(team: string) {
  return `${BASE}/teams/${encodeURIComponent(team)}`
}

export function ListTeamClusters(region?: string) {
  return teamRequest<TeamClustersResponse>(
    'get',
    `${BASE}/clusters?${toQuery({region: region || selectedRegion()})}`,
  )
}

export function ListTeams(target: TeamTarget, team?: string) {
  return teamRequest<TeamListResponse>(
    'get',
    `${BASE}/teams?${toQuery({...targetParams(target), team})}`,
  )
}

export function CreateTeam(target: TeamTarget, body: CreateTeamRequest) {
  return teamRequest<TeamWriteResponse>(
    'post',
    `${BASE}/teams?${toQuery(targetParams(target))}`,
    body,
  )
}

export function UpdateTeam(
  target: TeamTarget,
  team: string,
  body: UpdateTeamRequest,
) {
  return teamRequest<TeamWriteResponse>(
    'patch',
    `${teamPath(team)}?${toQuery(targetParams(target))}`,
    body,
  )
}

export function DeleteTeam(target: TeamTarget, team: string) {
  return teamRequest<DeleteTeamResponse>(
    'delete',
    `${teamPath(team)}?${toQuery(targetParams(target))}`,
  )
}

/** Lifecycle-managed teams add members by email; legacy teams by Linux user name. */
export function AddTeamMember(
  target: TeamTarget,
  team: string,
  member: AddMemberRequest | {user: string},
) {
  return teamRequest<AddMemberResponse>(
    'post',
    `${teamPath(team)}/members?${toQuery(targetParams(target))}`,
    member,
  )
}

export function ListLifecycleTeams(target: TeamTarget) {
  return teamRequest<LifecycleListResponse>(
    'get',
    `${BASE}/lifecycle/teams?${toQuery(targetParams(target))}`,
  )
}

export function GetLifecycleTeam(target: TeamTarget, team: string) {
  return teamRequest<LifecycleTeamResponse>(
    'get',
    `${BASE}/lifecycle/teams/${encodeURIComponent(team)}?${toQuery(
      targetParams(target),
    )}`,
  )
}

export function CreateLifecycleTeam(
  target: TeamTarget,
  body: CreateLifecycleTeamRequest,
) {
  return teamRequest<LifecycleTeamResponse>(
    'post',
    `${BASE}/lifecycle/teams?${toQuery(targetParams(target))}`,
    body,
  )
}

export function AdvanceLifecycleTeam(target: TeamTarget, team: string) {
  return teamRequest<LifecycleTeamResponse>(
    'post',
    `${BASE}/lifecycle/teams/${encodeURIComponent(team)}/advance?${toQuery(
      targetParams(target),
    )}`,
    {},
  )
}

export function RemoveTeamMember(
  target: TeamTarget,
  team: string,
  user: string,
) {
  return teamRequest<TeamWriteResponse>(
    'delete',
    `${teamPath(team)}/members/${encodeURIComponent(user)}?${toQuery(
      targetParams(target),
    )}`,
  )
}

export function GetTeamUsage(
  target: TeamTarget,
  start: string,
  end: string,
  team?: string,
) {
  return teamRequest<TeamUsageResponse>(
    'get',
    `${BASE}/usage?${toQuery({...targetParams(target), start, end, team})}`,
  )
}

export function GetTeamTrend(
  target: TeamTarget,
  months: number,
  team?: string,
) {
  return teamRequest<TeamTrendResponse>(
    'get',
    `${BASE}/trend?${toQuery({...targetParams(target), months, team})}`,
  )
}

export function GetCatalog(target: TeamTarget) {
  return teamRequest<CatalogResponse>(
    'get',
    `${BASE}/catalog?${toQuery(targetParams(target))}`,
  )
}
/** 机型与节点: specs, capacity status, node states and per-team use. Admins see every team, leads their own. */
export function GetCapacity(target: TeamTarget, team?: string) {
  return teamRequest<CapacityResponse>(
    'get',
    `${BASE}/capacity?${toQuery({...targetParams(target), team})}`,
  )
}
/** Lifecycle (email) members only: disable the PCUI and AD login and stop or drain the Slurm association. */
export function OffboardTeamMember(
  target: TeamTarget,
  team: string,
  user: string,
  jobs: OffboardJobs,
) {
  return teamRequest<OffboardResponse>(
    'post',
    `${teamPath(team)}/members/${encodeURIComponent(user)}/offboard?${toQuery(
      targetParams(target),
    )}`,
    {jobs},
  )
}
export function ReenableTeamMember(
  target: TeamTarget,
  team: string,
  user: string,
) {
  return teamRequest<OffboardResponse>(
    'post',
    `${teamPath(team)}/members/${encodeURIComponent(user)}/reenable?${toQuery(
      targetParams(target),
    )}`,
    {},
  )
}
/** Admin only. The audit log is not tied to a cluster. */
export function ListAudit(filters: AuditFilters) {
  return teamRequest<AuditResponse>(
    'get',
    `${BASE}/audit?${toQuery({...filters})}`,
  )
}
/** Job metadata of one team (never scripts or output). */
export function ListLeadJobs(target: TeamTarget, team: string) {
  return teamRequest<LeadJobsResponse>(
    'get',
    `${BASE}/lead/jobs?${toQuery({...targetParams(target), team})}`,
  )
}
export function GetLeadQuota(target: TeamTarget, team: string) {
  return teamRequest<LeadQuotaResponse>(
    'get',
    `${BASE}/lead/quota?${toQuery({...targetParams(target), team})}`,
  )
}
export function errorMessage(error: unknown): string {
  const axiosError = error as AxiosError<{message?: string}>
  return (
    axiosError?.response?.data?.message ||
    (error as Error)?.message ||
    String(error)
  )
}
