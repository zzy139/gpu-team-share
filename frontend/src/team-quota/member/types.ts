// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {
  BudgetBanner,
  CapacityStatus,
  CatalogType,
  CostSources,
  Team,
  TeamCluster,
  TeamUsage,
  TypeUsageFigures,
  UnmanagedQueue,
  UsageFigures,
} from '../types'

export interface MemberTarget {
  clusterName: string
  region?: string
  team?: string
}

export interface MemberTeam {
  team: string
  team_path: string
}

export interface MemberMe {
  email: string
  user: string
  teams: MemberTeam[]
  clusters: TeamCluster[]
}

/** A catalog instance type with the team's machine count, machines running and machines left. */
export interface MemberType extends CatalogType {
  limit: number
  running: number
  remaining: number
  borrow_allowed: boolean
  /** limit > 0 */
  has_quota?: boolean
  status?: CapacityStatus | null
}
export interface MemberLimits {
  nodes_max: number
  /** Borrowed jobs run at most this long (240); own-quota jobs have no time limit unless the member sets one. */
  borrow_max_minutes: number
}
export type EnvScope = 'team' | 'shared'
export interface EnvRef {
  scope: EnvScope
  team: string
  name: string
  version: string
}
export interface EnvDetected {
  cuda?: string | null
  python?: string | null
}
export interface EnvOption extends EnvRef {
  kind: EnvKind | null
  description: string | null
  detected: EnvDetected | null
  visibility?: EnvVisibility
}
export interface MemberOptions {
  types: MemberType[]
  unmanaged_queues: UnmanagedQueue[]
  envs: EnvOption[]
  envs_error: string | null
  limits: MemberLimits
  team: string
  team_root: string
  shared_root: string
  envs_root?: string
  budget: BudgetBanner | null
}
/** Badges of a job from the stuck-job alerts: 疑似空闲 / 已运行超24小时 / 暂无容量. */
export type JobAlert = 'idle' | 'long_run' | 'no_capacity'
export interface MemberJob {
  job_id: string
  name: string
  account: string
  partition: string
  instance_type?: string | null
  state: string
  reason: string | null
  reason_text: string
  submit: string | null
  start: string | null
  time_limit: string | null
  elapsed: string | null
  gres: string | null
  cpus: string | null
  memory: string | null
  qos: string | null
  alerts?: JobAlert[]
}

export interface MemberHistoryJob {
  job_id: string
  name: string
  account: string
  partition: string
  instance_type?: string | null
  qos: string | null
  state: string
  reason: string | null
  reason_text: string
  submit: string | null
  start: string | null
  end: string | null
  elapsed_seconds: number | null
  exit_code: string | null
  alloc_tres: string | null
  workdir: string | null
}

export interface MemberJobsResponse<T> {
  jobs: T[]
}

export interface MemberJobDetail {
  job_id: string
  name: string | null
  user: string
  account: string | null
  partition: string | null
  qos: string | null
  state: string | null
  reason: string | null
  reason_text: string
  submit: string | null
  start: string | null
  end: string | null
  time_limit: string | null
  elapsed: string | null
  exit_code: string | null
  tres: string | null
  workdir: string | null
  command: string | null
  stdout_path: string | null
  stderr_path: string | null
  team_root: string
  /** Whether the standard output can be shown: not_started (job not running yet), ok, env_deleted (environment
   * build whose version was deleted), never_started (ended without running), missing (ran, file absent). */
  output_state?: OutputState
  env_deleted?: {
    by: string | null
    at: string | null
    team: string
    name: string
    version: string
  } | null
  /** True while the job is not in a final state: the end time is only a projection. */
  end_is_projected?: boolean
  alerts?: JobAlert[]
}
export type OutputState =
  | 'not_started'
  | 'ok'
  | 'env_deleted'
  | 'never_started'
  | 'missing'

export interface MemberFileChunk {
  path: string
  size: number
  offset: number
  length: number
  content_b64: string
  truncated: boolean
  limit: number
}

export interface MemberDirEntry {
  type: 'file' | 'dir' | 'link' | 'other'
  size: number
  mtime: number | null
  name: string
}

export interface MemberDirListing {
  path: string
  entries: MemberDirEntry[]
  truncated: boolean
}

/** The portal never sends cpus, mem_gb or gpus_per_node: a job gets whole machines of the chosen type. */
export interface SubmitJobRequest {
  team?: string
  instance_type: string
  nodes: number
  /** Only from 高级选项 (own quota, optional) or the borrow cap (at most 240). */
  time_minutes?: number
  borrow: boolean
  job_name?: string
  env?: EnvRef | null
  workdir?: string
  script_path?: string
  script_content?: string
}
/** Prefilled submit body of an earlier job; ``instance_type`` is null when its queue is not in the catalog. */
export type ResubmitJob = Omit<SubmitJobRequest, 'instance_type'> & {
  instance_type: string | null
  // older templates and API clients may still carry these; the form ignores them
  gpus_per_node?: number
  cpus?: number
  mem_gb?: number
}
export interface ResubmitResponse {
  job_id: string
  partition: string
  job: ResubmitJob
}
export interface SubmitJobResponse {
  job_id: string
  script_path: string | null
}

export interface MemberOwnUsage extends UsageFigures {
  user: string
  by_type: TypeUsageFigures[]
}
export type MemberQuota = Omit<Team, 'members' | 'running_by_type'> & {
  running_by_type: Team['running_by_type']
}
export interface MemberTeamResponse {
  team: string
  quota: MemberQuota | null
  catalog: CatalogType[]
  usage: TeamUsage | null
  mine: MemberOwnUsage
  period: {start: string; end: string}
  billed_available: boolean
  sources: CostSources
  notes: string[]
  budget: BudgetBanner | null
}
export interface MemberBudgetResponse {
  team: string
  budget: BudgetBanner | null
}
// --- templates ---
export interface JobTemplate {
  id: string
  name: string
  job: ResubmitJob
  created_at: string
  updated_at: string
}
export interface TemplatesResponse {
  templates: JobTemplate[]
  max: number
}
// --- notifications ---
export type NotificationKind =
  | 'job'
  | 'budget'
  | 'gpu_idle'
  | 'long_run'
  | 'no_capacity'
export interface PortalNotification {
  id: string
  kind: NotificationKind
  job_id: string | null
  name: string | null
  state: string | null
  exit_code: string | null
  partition: string | null
  instance_type: string | null
  env: string | null
  end: string | null
  team: string | null
  /** Alerts: hours running (gpu_idle, long_run) or pending (no_capacity), idle minutes, the Chinese text. */
  hours?: number | null
  minutes?: number | null
  text?: string | null
  unread: boolean
}
export interface NotificationsResponse {
  items: PortalNotification[]
  unread: number
}
export interface Preferences {
  job_notify: boolean
  email_jobs: boolean
  email_budget: boolean
  /** E-mail for GPU idle / long-running / no-capacity alerts (on by default). */
  email_alerts: boolean
}
export interface PreferencesResponse extends Preferences {
  email: {address: string; status: 'none' | 'pending' | 'confirmed'}
}
// --- runtime environments ---
export type EnvKind = 'container' | 'conda'
/** Who can use every version of an environment name: 仅本团队 (team) or 所有人 (all). */
export type EnvVisibility = 'team' | 'all'
export type EnvSource = 'image' | 'dockerfile' | 'conda'
export type EnvStatus =
  | 'queued'
  | 'building'
  | 'succeeded'
  | 'failed'
  | 'unknown'
export interface EnvEntry extends EnvRef {
  id: string
  path: string
  owner: string | null
  kind: EnvKind | null
  source: EnvSource | null
  builder: 'slurm' | 'codebuild' | null
  image: string | null
  ref: string | null
  status: EnvStatus
  failure: string | null
  created_at: string | null
  queued_at: string | null
  started_at: string | null
  finished_at: string | null
  size_bytes: number | null
  description: string | null
  software: string | null
  python: string | null
  attempt: number | null
  job_id: string | null
  build_id: string | null
  progress: string | null
  log_path: string | null
  visibility: EnvVisibility
  can: {delete: boolean; retry: boolean}
  can_change_visibility: boolean
  /** Chinese reason for every action the caller may not take (delete, retry, visibility). */
  disabled_reasons: Partial<Record<'delete' | 'retry' | 'visibility', string>>
}
export interface EnvUsage {
  total_bytes: number
  cap_gb: number | null
  percent: number | null
  level: 'warning' | 'exceeded' | null
}
export interface EnvListResponse {
  team: string
  /** The team's environments and the 所有人 environments of other teams (``team`` field). */
  envs: EnvEntry[]
  /** Teams present in the list, the caller's team first (for the filter). */
  teams: string[]
  truncated: boolean
  usage: EnvUsage
  warning: string | null
  roles: {member: boolean; lead: boolean; admin: boolean}
  limits: {
    spec_max_bytes: number
    dockerfile_max_bytes: number
    registries: string[]
    team_repository: string
  }
}
export interface EnvCreateRequest {
  team?: string
  name: string
  version: string
  source: EnvSource
  image?: string
  dockerfile?: string
  environment_yml?: string
  requirements_txt?: string
  python?: string
  description?: string
  software?: string
  visibility?: EnvVisibility
}
export interface EnvCreateResponse {
  env: {
    id: string
    status: EnvStatus
    job_id: string | null
    build_id: string | null
    log_path: string | null
  }
  usage: EnvUsage
  warning: string | null
}
