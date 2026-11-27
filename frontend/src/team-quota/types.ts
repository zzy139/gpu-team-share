// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
export interface TeamTarget {
  clusterName: string
  region?: string
}
export interface TeamCluster {
  name: string
  region: string
}
export interface TeamClustersResponse {
  clusters: TeamCluster[]
}
/** One instance type that team quotas count: a queue with exactly one compute resource and one instance type. */
export interface CatalogType {
  instance_type: string
  partition: string
  gpus: number
  vcpus: number | null
  memory_gib: number | null
  price_per_hour: number | null
  /** e.g. 'NVIDIA L40S'; absent when the instance type was not described. */
  gpu_model?: string | null
  /** GPU memory per GPU in data-sheet GB (L40S = 48). */
  gpu_memory_gb?: number | null
  /** GPU memory per GPU in GiB as DescribeInstanceTypes reports it. */
  gpu_memory_gib?: number | null
}
/** Capacity of one instance type: 有空闲 / 需启动约5分钟 / 暂无容量 / 全部在用，需排队; null = unknown. */
export type CapacityStatus = 'available' | 'startable' | 'no_capacity' | 'busy'
export interface CapacityNodeRow {
  state: string
  count: number
  reason: string | null
  /** Slurm host list, e.g. 'g6e-xlarge-dy-g6e-xlarge-[1-4]'. */
  nodes: string | null
}
export interface CapacityTeamRow {
  team: string
  limit: number
  running: number
  pending: number
  borrowed: number
}
export interface CapacityType extends CatalogType {
  status: CapacityStatus | null
  no_capacity: boolean
  node_total: number
  nodes: CapacityNodeRow[]
  teams: CapacityTeamRow[]
}
export interface CapacityResponse {
  types: CapacityType[]
  teams: string[]
  unmanaged_queues: UnmanagedQueue[]
  /** Admin only: node states of the queues outside the catalog. */
  partitions?: {partition: string; nodes: CapacityNodeRow[]}[]
}
export type UnmanagedReason =
  | 'multi_type'
  | 'no_gpu'
  | 'duplicate_type'
  | 'unknown_type'
  | 'invalid_name'
export interface UnmanagedQueue {
  queue: string
  reason: UnmanagedReason
  instance_types: string[]
}
export interface CatalogResponse {
  types: CatalogType[]
  unmanaged_queues: UnmanagedQueue[]
}
/** {instance_type: machines}; 0 = the team may not use that type. */
export type TypeLimits = Record<string, number>
export interface TypeUserUsage {
  running: number
  pending: number
}
export interface TypeUsage {
  partition: string
  running: number
  pending: number
  borrowed: number
  users?: Record<string, TypeUserUsage>
}
export interface TeamRunning {
  jobs: number
  machines: number
  gpus: number
}
export type OffboardState = 'finishing' | 'offboarded'
export interface Team {
  name: string
  fairshare: number | null
  fairshare_share: number | null
  qos: string | null
  type_limits: TypeLimits
  running_by_type: Record<string, TypeUsage>
  members: string[]
  running: TeamRunning
  budget_usd: number | null
  env_storage_cap_gb: number | null
  /** Admin only. */
  offboarded?: Record<string, OffboardState>
}
export interface TeamListResponse {
  teams: Team[]
  catalog: CatalogType[]
  unmanaged_queues: UnmanagedQueue[]
  editable: boolean
}
export interface CreateTeamRequest {
  name: string
  fairshare: number
  type_limits: TypeLimits
  budget_usd?: number | null
}
export interface UpdateTeamRequest {
  fairshare?: number
  type_limits?: TypeLimits
  budget_usd?: number | null
  env_storage_cap_gb?: number | null
}
export interface TeamWriteResponse {
  team: string
  user?: string
  result: string
}
/** Usage of one instance type, or the totals of a team or member. */
export interface UsageFigures {
  instance_hours: number
  gpu_hours: number
  estimate_cost: number
  unpriced_instance_hours: number
}
export interface TypeUsageFigures extends UsageFigures {
  instance_type: string
}
export interface TeamUsage extends UsageFigures {
  name: string
  by_type: TypeUsageFigures[]
  cost_share: number | null
  compute_cost: number | null
  shared_cost: number | null
}
export interface MemberUsage extends UsageFigures {
  team: string
  user: string
  by_type: TypeUsageFigures[]
}
export interface UsageTotals {
  compute_cost: number | null
  shared_cost: number | null
  total_cost: number | null
  instance_hours: number
  gpu_hours: number
  estimate_cost: number
  unallocated_compute_cost: number | null
  unallocated_shared_cost: number | null
}
export interface CostSources {
  billed: string
  estimate: string
}
export interface TeamUsageResponse {
  period: {start: string; end: string}
  project: string
  teams: TeamUsage[]
  by_member: MemberUsage[]
  totals?: UsageTotals
  billed_available: boolean
  prices: Record<string, number>
  sources: CostSources
  notes: string[]
}
export interface TrendTeam {
  name: string
  instance_hours: number
  gpu_hours: number
  estimate_cost: number
  compute_cost: number | null
  shared_cost: number | null
}
export interface TrendMonth {
  month: string
  teams: TrendTeam[]
  totals?: {compute_cost: number | null; shared_cost: number | null}
}
export interface TeamTrendResponse {
  months: TrendMonth[]
  billed_available: boolean
  sources: CostSources
  notes: string[]
}
// --- team lifecycle (admin) ---
export type LifecycleStepId = 'ad' | 'storage' | 'ecr' | 'cognito' | 'slurm'
export type LifecycleStepStatus = 'done' | 'pending' | 'in_progress' | 'failed'
export interface LifecycleStep {
  id: LifecycleStepId
  status: LifecycleStepStatus
  message?: string
}
export type LifecycleState = 'provisioning' | 'active' | 'deleted'
export interface LifecycleTeamResponse {
  team: string
  state: LifecycleState
  steps: LifecycleStep[]
  complete?: boolean
}
export interface LifecycleRecord {
  team: string
  state: LifecycleState
  ad_group: string
  team_path: string
  ecr_repo: string
  env_storage_cap_gb?: number | null
  cognito_group: string
  member_group: string
  qos: string
  created_at: string
  updated_at: string
  deleted_at: string | null
}
export interface LifecycleListResponse {
  configured: boolean
  records: LifecycleRecord[]
  email_domains: string[]
}
export interface LifecycleMember {
  email: string
  link_existing?: boolean
}
export interface CreateLifecycleTeamRequest extends CreateTeamRequest {
  env_storage_cap_gb?: number | null
  members: LifecycleMember[]
  lead?: string
}
export interface RetainedResources {
  ad_group?: string
  ad_users?: string[]
  team_path?: string
  ecr_repo?: string
  secrets?: string[]
  cognito_users?: string[]
}
export interface DeleteTeamResponse extends TeamWriteResponse {
  removed: string[]
  retained: RetainedResources
}
export interface AddMemberRequest {
  email: string
  link_existing?: boolean
}
export interface MemberStep {
  id: 'ad' | 'cognito' | 'slurm'
  status: LifecycleStepStatus
  message?: string
}
export interface AddMemberResponse extends TeamWriteResponse {
  email?: string
  steps?: MemberStep[]
}
export type OffboardJobs = 'cancel' | 'finish'
export interface OffboardStep {
  id: string
  status: LifecycleStepStatus
  message?: string
}
export interface OffboardResponse {
  team: string
  user: string
  state: OffboardState | 'active'
  steps: OffboardStep[]
}
// --- audit (admin) ---
export interface AuditEntry {
  time: string
  actor: string | null
  actor_email: string | null
  action: string
  cluster: string | null
  team: string | null
  params: string | null
  result: string
  error: string | null
}
export interface AuditFilters {
  month?: string
  from?: string
  to?: string
  actor?: string
  team?: string
  action?: string
}
export interface AuditResponse {
  months: string[]
  entries: AuditEntry[]
  truncated: boolean
}
// --- team lead pages ---
export interface LeadJob {
  job_id: string
  user: string
  name: string
  partition: string
  instance_type: string | null
  state: string
  reason: string | null
  reason_text: string
  submit: string | null
  start: string | null
  time_limit: string | null
  elapsed: string | null
  nodes: string | number | null
  qos: string | null
}
export interface LeadJobsResponse {
  team: string
  jobs: LeadJob[]
}
export interface BudgetBanner {
  team: string
  month: string
  budget_usd: number
  spent_usd: number | null
  percent: number | null
  level: 'warning' | 'exceeded' | null
  updated_at: string | null
}
export interface QuotaHolder {
  user: string
  running: number
  pending: number
}
export interface LeadQuotaType {
  instance_type: string
  partition: string
  gpus: number
  limit: number
  running: number
  pending: number
  borrowed: number
  holders: QuotaHolder[]
}
export interface LeadQuotaResponse {
  team: string
  types: LeadQuotaType[]
  unmanaged_queues: UnmanagedQueue[]
  budget: BudgetBanner | null
}
