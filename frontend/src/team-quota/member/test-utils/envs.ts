import {EnvEntry, EnvListResponse} from '../types'

const can = {delete: false, retry: false}
const OTHER_TEAM = '这是其他团队公开的环境，只能使用'

export function envEntry(change: Partial<EnvEntry>): EnvEntry {
  const base: EnvEntry = {
    id: 'team/teama/cuda/12.4',
    scope: 'team',
    team: 'teama',
    name: 'cuda',
    version: '12.4',
    path: '/efs/envs/teama/cuda/12.4',
    owner: 'carol',
    kind: 'container',
    source: 'image',
    builder: 'slurm',
    image: 'nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04',
    ref: 'docker://nvcr.io#nvidia/cuda:12.4.1-base-ubuntu22.04',
    status: 'succeeded',
    failure: null,
    created_at: '2026-11-04T01:00:00Z',
    queued_at: null,
    started_at: null,
    finished_at: '2026-11-04T01:10:00Z',
    size_bytes: 2_500_000_000,
    description: 'CUDA base',
    software: null,
    python: null,
    attempt: 1,
    job_id: '41',
    build_id: null,
    progress: null,
    log_path: '/efs/envs/teama/cuda/12.4/build.log',
    visibility: 'team',
    can,
    can_change_visibility: false,
    disabled_reasons: {},
  }
  const entry = {...base, ...change}
  entry.id = `${entry.scope}/${entry.team}/${entry.name}/${entry.version}`
  return entry
}

export const envList: EnvListResponse = {
  team: 'teama',
  envs: [
    envEntry({
      can: {...can, delete: true},
      can_change_visibility: true,
      disabled_reasons: {retry: '只有构建失败的版本可以重新构建'},
    }),
    envEntry({
      name: 'af',
      version: '2.3',
      kind: 'conda',
      source: 'conda',
      status: 'failed',
      failure: 'micromamba: package not found',
      owner: 'carol',
      size_bytes: null,
      can: {...can, delete: true, retry: true},
      can_change_visibility: true,
    }),
    envEntry({
      name: 'torch',
      version: '2.4',
      owner: 'dave',
      status: 'building',
      progress: 'BUILD',
      builder: 'codebuild',
      log_path: null,
      disabled_reasons: {
        delete: '只有这个版本的创建者、团队负责人或管理员可以删除',
        retry: '只有创建者可以重新构建',
        visibility: '只有这个环境的创建者、团队负责人或管理员可以更改可见范围',
      },
    }),
    envEntry({
      team: 'teamb',
      name: 'alphafold',
      version: '2.3',
      owner: 'erin',
      path: '/efs/envs/teamb/alphafold/2.3',
      log_path: null,
      size_bytes: 9_000_000_000,
      visibility: 'all',
      description: 'AlphaFold',
      disabled_reasons: {
        delete: OTHER_TEAM,
        retry: OTHER_TEAM,
        visibility: OTHER_TEAM,
      },
    }),
  ],
  teams: ['teama', 'teamb'],
  truncated: false,
  usage: {
    total_bytes: 170_000_000_000,
    cap_gb: 200,
    percent: 85,
    level: 'warning',
  },
  warning: '团队已使用环境存储上限 200 GB 的 85.0%，请尽快删除旧版本',
  roles: {member: true, lead: false, admin: false},
  limits: {
    spec_max_bytes: 65536,
    dockerfile_max_bytes: 15000,
    registries: ['docker.io', 'nvcr.io', 'public.ecr.aws', 'ghcr.io'],
    team_repository:
      '111122223333.dkr.ecr.us-west-2.amazonaws.com/hpc-poc-teama-envs',
  },
}
