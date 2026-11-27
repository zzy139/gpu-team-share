// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import React from 'react'
import {useMutation, useQuery, useQueryClient} from 'react-query'
import {setState, useState} from '../../store'
import {
  CancelMemberJob,
  CreateEnv,
  CreateTemplate,
  DeleteEnv,
  DeleteTemplate,
  GetMemberBudget,
  GetMemberJob,
  GetMemberMe,
  GetMemberOptions,
  GetMemberTeam,
  GetPreferences,
  ListEnvs,
  ListMemberHistory,
  ListMemberJobs,
  ListNotifications,
  ListTemplates,
  MarkNotificationsRead,
  RetryEnv,
  SetEnvVisibility,
  SubmitMemberJob,
  UpdatePreferences,
  UpdateTemplate,
} from './api'
import {
  EnvCreateRequest,
  EnvRef,
  EnvVisibility,
  MemberJobDetail,
  MemberTarget,
  Preferences,
  ResubmitJob,
  SubmitJobRequest,
} from './types'

export const MEMBER_QUERY_KEY = 'TEAM_QUOTA_MEMBER'
export const SELECTED_MEMBER_TEAM_PATH = ['app', 'teamQuota', 'memberTeam']
/** Job pages refresh every 15 s while the page is visible (and, for one job, until it is final). */
export const JOBS_REFRESH_MS = 15000
const NOTIFICATIONS_REFRESH_MS = 60000
const ENVS_REFRESH_MS = 30000

function key(target: MemberTarget | null) {
  return target
    ? [target.clusterName, target.region || '', target.team || '']
    : []
}

// Slurm states after which a job does not change any more.
const FINAL_STATES = new Set([
  'COMPLETED',
  'FAILED',
  'TIMEOUT',
  'CANCELLED',
  'OUT_OF_MEMORY',
  'NODE_FAIL',
  'BOOT_FAIL',
  'PREEMPTED',
  'DEADLINE',
  'REVOKED',
  'SPECIAL_EXIT',
])
export function isFinalState(state: string | null | undefined) {
  return !!state && FINAL_STATES.has(state.split(' ')[0].toUpperCase())
}

/** False while the browser tab is hidden (document.hidden), so polling pauses in the background. */
export function useDocumentVisible() {
  const [visible, setVisible] = React.useState(
    typeof document === 'undefined' ? true : !document.hidden,
  )
  React.useEffect(() => {
    const onChange = () => setVisible(!document.hidden)
    document.addEventListener('visibilitychange', onChange)
    return () => document.removeEventListener('visibilitychange', onChange)
  }, [])
  return visible
}

export function useMemberMeQuery() {
  return useQuery([MEMBER_QUERY_KEY, 'me'], GetMemberMe, {staleTime: 60000})
}

/** Cluster and team of the member pages. Members cannot list clusters, so both come from /member/me. */
export function useMemberTarget(): {
  target: MemberTarget | null
  teams: string[]
  setTeam: (team: string) => void
  isLoading: boolean
  error: unknown
} {
  const {data, isLoading, error} = useMemberMeQuery()
  const selected: string | undefined = useState(SELECTED_MEMBER_TEAM_PATH)
  const teams = React.useMemo(
    () => (data?.teams || []).map(t => t.team),
    [data],
  )
  const cluster = data?.clusters?.[0]
  const team = selected && teams.includes(selected) ? selected : teams[0]
  const target = React.useMemo(
    () =>
      cluster && team
        ? {clusterName: cluster.name, region: cluster.region, team}
        : null,
    [cluster, team],
  )
  const setTeam = React.useCallback(
    (value: string) => setState(SELECTED_MEMBER_TEAM_PATH, value),
    [],
  )
  return {target, teams, setTeam, isLoading, error}
}

export function useMemberOptionsQuery(target: MemberTarget | null) {
  return useQuery(
    [MEMBER_QUERY_KEY, 'options', ...key(target)],
    () => GetMemberOptions(target!),
    {enabled: !!target, staleTime: 5 * 60000},
  )
}

export function useMemberJobsQuery(target: MemberTarget | null) {
  const visible = useDocumentVisible()
  return useQuery(
    [MEMBER_QUERY_KEY, 'jobs', ...key(target)],
    () => ListMemberJobs(target!),
    {enabled: !!target, refetchInterval: visible ? JOBS_REFRESH_MS : false},
  )
}

export function useMemberHistoryQuery(
  target: MemberTarget | null,
  start: string,
  end: string,
) {
  return useQuery(
    [MEMBER_QUERY_KEY, 'history', ...key(target), start, end],
    () => ListMemberHistory(target!, start, end),
    {enabled: !!target && !!start && !!end},
  )
}

/** One job; refreshed every 15 s while it is not final and the page is visible. */
export function useMemberJobQuery(target: MemberTarget | null, jobId: string) {
  const visible = useDocumentVisible()
  return useQuery(
    [MEMBER_QUERY_KEY, 'job', ...key(target), jobId],
    () => GetMemberJob(target!, jobId),
    {
      enabled: !!target && !!jobId,
      refetchInterval: (data: MemberJobDetail | undefined) =>
        visible && data && !isFinalState(data.state) ? JOBS_REFRESH_MS : false,
    },
  )
}

export function useMemberTeamQuery(
  target: MemberTarget | null,
  start: string,
  end: string,
) {
  return useQuery(
    [MEMBER_QUERY_KEY, 'team', ...key(target), start, end],
    () => GetMemberTeam(target!, start, end),
    {enabled: !!target && !!start && !!end, staleTime: 5 * 60000},
  )
}

function useMemberMutation<TVariables, TResult>(
  mutationFn: (variables: TVariables) => Promise<TResult>,
) {
  const queryClient = useQueryClient()
  return useMutation(mutationFn, {
    onSettled: () => queryClient.invalidateQueries([MEMBER_QUERY_KEY, 'jobs']),
  })
}

export function useSubmitJobMutation(target: MemberTarget | null) {
  return useMemberMutation((body: SubmitJobRequest) =>
    SubmitMemberJob(target!, body),
  )
}

export function useCancelJobMutation(target: MemberTarget | null) {
  return useMemberMutation((jobId: string) => CancelMemberJob(target!, jobId))
}
export function useMemberBudgetQuery(target: MemberTarget | null) {
  return useQuery(
    [MEMBER_QUERY_KEY, 'budget', ...key(target)],
    () => GetMemberBudget(target!),
    {enabled: !!target, staleTime: 5 * 60000},
  )
}
export function useTemplatesQuery(target: MemberTarget | null) {
  return useQuery(
    [MEMBER_QUERY_KEY, 'templates'],
    () => ListTemplates(target),
    {enabled: !!target},
  )
}
function useTemplateMutation<TVariables, TResult>(
  mutationFn: (variables: TVariables) => Promise<TResult>,
) {
  const queryClient = useQueryClient()
  return useMutation(mutationFn, {
    onSettled: () =>
      queryClient.invalidateQueries([MEMBER_QUERY_KEY, 'templates']),
  })
}
export function useCreateTemplateMutation(target: MemberTarget | null) {
  return useTemplateMutation((body: {name: string; job: ResubmitJob}) =>
    CreateTemplate(target, body),
  )
}
export function useUpdateTemplateMutation(target: MemberTarget | null) {
  return useTemplateMutation(
    ({id, body}: {id: string; body: {name?: string; job?: ResubmitJob}}) =>
      UpdateTemplate(target, id, body),
  )
}
export function useDeleteTemplateMutation(target: MemberTarget | null) {
  return useTemplateMutation((id: string) => DeleteTemplate(target, id))
}
export function useNotificationsQuery(target: MemberTarget | null) {
  return useQuery(
    [MEMBER_QUERY_KEY, 'notifications'],
    () => ListNotifications(target),
    {enabled: !!target, refetchInterval: NOTIFICATIONS_REFRESH_MS},
  )
}
export function useMarkReadMutation(target: MemberTarget | null) {
  const queryClient = useQueryClient()
  return useMutation(() => MarkNotificationsRead(target), {
    onSettled: () =>
      queryClient.invalidateQueries([MEMBER_QUERY_KEY, 'notifications']),
  })
}
export function usePreferencesQuery(
  target: MemberTarget | null,
  enabled: boolean,
) {
  return useQuery(
    [MEMBER_QUERY_KEY, 'preferences'],
    () => GetPreferences(target),
    {enabled: !!target && enabled},
  )
}
export function useUpdatePreferencesMutation(target: MemberTarget | null) {
  const queryClient = useQueryClient()
  return useMutation(
    (body: Partial<Preferences>) => UpdatePreferences(target, body),
    {
      onSuccess: data => {
        queryClient.setQueryData([MEMBER_QUERY_KEY, 'preferences'], data)
      },
    },
  )
}
export function useEnvsQuery(target: MemberTarget | null) {
  return useQuery(
    [MEMBER_QUERY_KEY, 'envs', ...key(target)],
    () => ListEnvs(target!),
    {enabled: !!target, refetchInterval: ENVS_REFRESH_MS},
  )
}
function useEnvMutation<TVariables, TResult>(
  mutationFn: (variables: TVariables) => Promise<TResult>,
) {
  const queryClient = useQueryClient()
  return useMutation(mutationFn, {
    onSettled: () => {
      queryClient.invalidateQueries([MEMBER_QUERY_KEY, 'envs'])
      queryClient.invalidateQueries([MEMBER_QUERY_KEY, 'options'])
    },
  })
}
export function useCreateEnvMutation(target: MemberTarget | null) {
  return useEnvMutation((body: EnvCreateRequest) => CreateEnv(target!, body))
}
export function useRetryEnvMutation(target: MemberTarget | null) {
  return useEnvMutation((env: EnvRef) => RetryEnv(target!, env))
}
export function useDeleteEnvMutation(target: MemberTarget | null) {
  return useEnvMutation((env: EnvRef) => DeleteEnv(target!, env))
}
export function useEnvVisibilityMutation(target: MemberTarget | null) {
  return useEnvMutation(
    ({
      env,
      visibility,
    }: {
      env: {team: string; name: string}
      visibility: EnvVisibility
    }) => SetEnvVisibility(target!, env, visibility),
  )
}
