// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
// Member portal API. The server derives the cluster user from the login; no user name is ever sent.
import {teamRequest, toQuery} from '../api'
import {
  EnvCreateRequest,
  EnvCreateResponse,
  EnvListResponse,
  EnvRef,
  EnvVisibility,
  JobTemplate,
  MemberBudgetResponse,
  MemberDirListing,
  MemberFileChunk,
  MemberHistoryJob,
  MemberJob,
  MemberJobDetail,
  MemberJobsResponse,
  MemberMe,
  MemberOptions,
  MemberTarget,
  MemberTeamResponse,
  NotificationsResponse,
  Preferences,
  PreferencesResponse,
  ResubmitJob,
  ResubmitResponse,
  SubmitJobRequest,
  SubmitJobResponse,
  TemplatesResponse,
} from './types'

const BASE = 'team-quota/member'

function params(target: MemberTarget) {
  return {
    cluster_name: target.clusterName,
    region: target.region,
    team: target.team,
  }
}

export function GetMemberMe() {
  return teamRequest<MemberMe>('get', `${BASE}/me`)
}

export function GetMemberOptions(target: MemberTarget) {
  return teamRequest<MemberOptions>(
    'get',
    `${BASE}/options?${toQuery(params(target))}`,
  )
}

export function ListMemberJobs(target: MemberTarget) {
  return teamRequest<MemberJobsResponse<MemberJob>>(
    'get',
    `${BASE}/jobs?${toQuery(params(target))}`,
  )
}

export function ListMemberHistory(
  target: MemberTarget,
  start: string,
  end: string,
) {
  return teamRequest<MemberJobsResponse<MemberHistoryJob>>(
    'get',
    `${BASE}/history?${toQuery({...params(target), start, end})}`,
  )
}

export function GetMemberJob(target: MemberTarget, jobId: string) {
  return teamRequest<MemberJobDetail>(
    'get',
    `${BASE}/jobs/${encodeURIComponent(jobId)}?${toQuery(params(target))}`,
  )
}

export function ReadMemberFile(
  target: MemberTarget,
  path: string,
  mode: 'tail' | 'offset',
  offset: number,
  limit: number,
) {
  return teamRequest<MemberFileChunk>(
    'get',
    `${BASE}/files?${toQuery({...params(target), path, mode, offset, limit})}`,
  )
}

export function ListMemberDir(target: MemberTarget, path: string) {
  return teamRequest<MemberDirListing>(
    'get',
    `${BASE}/dir?${toQuery({...params(target), path})}`,
  )
}

export function SubmitMemberJob(target: MemberTarget, body: SubmitJobRequest) {
  return teamRequest<SubmitJobResponse>(
    'post',
    `${BASE}/jobs?${toQuery(params(target))}`,
    body,
  )
}

export function CancelMemberJob(target: MemberTarget, jobId: string) {
  return teamRequest<{job_id: string; result: string}>(
    'post',
    `${BASE}/jobs/${encodeURIComponent(jobId)}/cancel?${toQuery(
      params(target),
    )}`,
    {},
  )
}

export function GetMemberTeam(
  target: MemberTarget,
  start: string,
  end: string,
) {
  return teamRequest<MemberTeamResponse>(
    'get',
    `${BASE}/team?${toQuery({...params(target), start, end})}`,
  )
}

export function GetResubmit(target: MemberTarget, jobId: string) {
  return teamRequest<ResubmitResponse>(
    'get',
    `${BASE}/jobs/${encodeURIComponent(jobId)}/resubmit?${toQuery(
      params(target),
    )}`,
  )
}
export function GetMemberBudget(target: MemberTarget) {
  return teamRequest<MemberBudgetResponse>(
    'get',
    `${BASE}/budget?${toQuery(params(target))}`,
  )
}
// Templates, notifications and preferences belong to the user, not to a cluster or team.
function regionParams(target: MemberTarget | null) {
  return toQuery({region: target?.region})
}
export function ListTemplates(target: MemberTarget | null) {
  return teamRequest<TemplatesResponse>(
    'get',
    `${BASE}/templates?${regionParams(target)}`,
  )
}
export function CreateTemplate(
  target: MemberTarget | null,
  body: {name: string; job: ResubmitJob},
) {
  return teamRequest<JobTemplate>(
    'post',
    `${BASE}/templates?${regionParams(target)}`,
    body,
  )
}
export function UpdateTemplate(
  target: MemberTarget | null,
  id: string,
  body: {name?: string; job?: ResubmitJob},
) {
  return teamRequest<JobTemplate>(
    'patch',
    `${BASE}/templates/${encodeURIComponent(id)}?${regionParams(target)}`,
    body,
  )
}
export function DeleteTemplate(target: MemberTarget | null, id: string) {
  return teamRequest<{id: string; result: string}>(
    'delete',
    `${BASE}/templates/${encodeURIComponent(id)}?${regionParams(target)}`,
  )
}
export function ListNotifications(target: MemberTarget | null) {
  return teamRequest<NotificationsResponse>(
    'get',
    `${BASE}/notifications?${regionParams(target)}`,
  )
}
export function MarkNotificationsRead(target: MemberTarget | null) {
  return teamRequest<{result: string}>(
    'post',
    `${BASE}/notifications/read?${regionParams(target)}`,
    {},
  )
}
export function GetPreferences(target: MemberTarget | null) {
  return teamRequest<PreferencesResponse>(
    'get',
    `${BASE}/preferences?${regionParams(target)}`,
  )
}
export function UpdatePreferences(
  target: MemberTarget | null,
  body: Partial<Preferences>,
) {
  return teamRequest<PreferencesResponse>(
    'put',
    `${BASE}/preferences?${regionParams(target)}`,
    body,
  )
}
export function ListEnvs(target: MemberTarget) {
  return teamRequest<EnvListResponse>(
    'get',
    `${BASE}/envs?${toQuery(params(target))}`,
  )
}
export function CreateEnv(target: MemberTarget, body: EnvCreateRequest) {
  return teamRequest<EnvCreateResponse>(
    'post',
    `${BASE}/envs?${toQuery(params(target))}`,
    body,
  )
}
function envPath(env: EnvRef) {
  return [env.scope, env.team, env.name, env.version]
    .map(encodeURIComponent)
    .join('/')
}
export function RetryEnv(target: MemberTarget, env: EnvRef) {
  return teamRequest<{id: string; result: string}>(
    'post',
    `${BASE}/envs/${envPath(env)}/retry?${toQuery(params(target))}`,
    {},
  )
}
/** Delete one version (its creator, a lead of the team or an admin). */
export function DeleteEnv(target: MemberTarget, env: EnvRef) {
  return teamRequest<{id: string; result: string}>(
    'delete',
    `${BASE}/envs/${envPath(env)}?${toQuery(params(target))}`,
  )
}
/** Change who can use every version of an environment name (creator, lead, admin). */
export function SetEnvVisibility(
  target: MemberTarget,
  env: {team: string; name: string},
  visibility: EnvVisibility,
) {
  return teamRequest<{
    team: string
    name: string
    visibility: EnvVisibility
    old: string | null
  }>(
    'put',
    `${BASE}/envs/team/${encodeURIComponent(env.team)}/${encodeURIComponent(
      env.name,
    )}/visibility?${toQuery(params(target))}`,
    {visibility},
  )
}
/** Decode base64 file content as UTF-8; invalid sequences become U+FFFD. */
export function decodeBase64Text(b64: string): string {
  const binary = atob(b64 || '')
  if (typeof TextDecoder !== 'undefined') {
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
    return new TextDecoder('utf-8', {fatal: false}).decode(bytes)
  }
  try {
    return decodeURIComponent(escape(binary))
  } catch {
    return binary.replace(/[\x80-\xff]/g, '\uFFFD')
  }
}

/** UTF-8 byte length of a string (the server limit for pasted scripts is in bytes). */
export function utf8Length(text: string): number {
  if (typeof TextEncoder !== 'undefined')
    return new TextEncoder().encode(text).length
  return unescape(encodeURIComponent(text)).length
}
