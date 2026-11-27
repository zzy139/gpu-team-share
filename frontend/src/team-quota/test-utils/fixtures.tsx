import {I18nextProvider} from 'react-i18next'
import {QueryClient, QueryClientProvider} from 'react-query'
import {Provider} from 'react-redux'
import {MemoryRouter} from 'react-router-dom'
import i18n from '../../i18n'
import {store} from '../../store'
import {
  CapacityResponse,
  CatalogType,
  Team,
  TeamListResponse,
  TeamUsageResponse,
  UnmanagedQueue,
} from '../types'

export function TestProviders({
  children,
  path = '/',
}: {
  children: React.ReactNode
  path?: string
}) {
  const queryClient = new QueryClient({
    defaultOptions: {queries: {retry: false}},
  })
  return (
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <Provider store={store}>
          <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>
        </Provider>
      </I18nextProvider>
    </QueryClientProvider>
  )
}

export const adminIdentity = {
  user_roles: ['admin'],
  username: 'admin',
  attributes: {email: 'admin@example.com'},
}
export const leadIdentity = {
  user_roles: ['team-teama'],
  username: 'lead',
  attributes: {email: 'lead@example.com'},
}

export const catalog: CatalogType[] = [
  {
    instance_type: 'g6e.xlarge',
    partition: 'g6e-xlarge',
    gpus: 1,
    vcpus: 4,
    memory_gib: 32,
    price_per_hour: 1.861,
  },
  {
    instance_type: 'g6e.2xlarge',
    partition: 'g6e-2xlarge',
    gpus: 1,
    vcpus: 8,
    memory_gib: 64,
    price_per_hour: 2.242,
  },
]
export const multiTypeQueue: UnmanagedQueue = {
  queue: 'mixed',
  reason: 'multi_type',
  instance_types: ['g6.xlarge', 'g6.2xlarge'],
}
export const buildQueue: UnmanagedQueue = {
  queue: 'build',
  reason: 'no_gpu',
  instance_types: ['m7i.xlarge'],
}
export const teama: Team = {
  name: 'teama',
  fairshare: 60,
  fairshare_share: 0.6,
  qos: 'teama-std',
  type_limits: {'g6e.xlarge': 2, 'g6e.2xlarge': 1},
  running_by_type: {
    'g6e.xlarge': {
      partition: 'g6e-xlarge',
      running: 1,
      pending: 1,
      borrowed: 0,
      users: {alice: {running: 1, pending: 1}},
    },
  },
  members: ['alice'],
  running: {jobs: 1, machines: 1, gpus: 1},
  budget_usd: 500,
  env_storage_cap_gb: 200,
  offboarded: {},
}
export const teamb: Team = {
  name: 'teamb',
  fairshare: 40,
  fairshare_share: 0.4,
  qos: 'teamb-std',
  type_limits: {'g6e.xlarge': 4, 'g6e.2xlarge': 0},
  running_by_type: {},
  members: ['bob'],
  running: {jobs: 0, machines: 0, gpus: 0},
  budget_usd: null,
  env_storage_cap_gb: null,
  offboarded: {},
}
export const adminTeams: TeamListResponse = {
  teams: [teama, teamb],
  catalog,
  unmanaged_queues: [buildQueue],
  editable: true,
}
export const leadTeams: TeamListResponse = {
  teams: [teama],
  catalog,
  unmanaged_queues: [buildQueue],
  editable: false,
}
const figures = (hours: number, cost: number) => ({
  instance_hours: hours,
  gpu_hours: hours,
  estimate_cost: cost,
  unpriced_instance_hours: 0,
})
export const usage: TeamUsageResponse = {
  period: {start: '2026-11-01', end: '2026-11-03'},
  project: 'hpc-poc',
  teams: [
    {
      name: 'teama',
      ...figures(2.5, 4.6525),
      by_type: [{instance_type: 'g6e.xlarge', ...figures(2.5, 4.6525)}],
      cost_share: 0.5556,
      compute_cost: 5.5556,
      shared_cost: 2.7778,
    },
  ],
  by_member: [
    {
      team: 'teama',
      user: 'alice',
      ...figures(1.25, 2.3263),
      by_type: [{instance_type: 'g6e.xlarge', ...figures(1.25, 2.3263)}],
    },
  ],
  totals: {
    compute_cost: 10,
    shared_cost: 5,
    total_cost: 15,
    instance_hours: 4.5,
    gpu_hours: 4.5,
    estimate_cost: 8.3745,
    unallocated_compute_cost: 0,
    unallocated_shared_cost: 0,
  },
  billed_available: true,
  prices: {'g6e.xlarge': 1.861},
  sources: {billed: 'cost-explorer', estimate: 'pricing-api'},
  notes: [],
}
export const capacity: CapacityResponse = {
  types: [
    {
      ...catalog[0],
      gpu_model: 'NVIDIA L40S',
      gpu_memory_gb: 48,
      status: 'available',
      no_capacity: false,
      node_total: 4,
      nodes: [
        {
          state: 'idle',
          count: 1,
          reason: null,
          nodes: 'g6e-xlarge-dy-g6e-xlarge-1',
        },
        {
          state: 'idle~',
          count: 3,
          reason: null,
          nodes: 'g6e-xlarge-dy-g6e-xlarge-[2-4]',
        },
      ],
      teams: [
        {team: 'teama', limit: 2, running: 1, pending: 1, borrowed: 0},
        {team: 'teamb', limit: 4, running: 0, pending: 0, borrowed: 0},
      ],
    },
    {
      ...catalog[1],
      gpu_model: 'NVIDIA L40S',
      gpu_memory_gb: 48,
      status: 'no_capacity',
      no_capacity: true,
      node_total: 2,
      nodes: [
        {
          state: 'down~',
          count: 2,
          reason:
            '(Code:InsufficientInstanceCapacity)Failure when resuming nodes',
          nodes: 'g6e-2xlarge-dy-g6e-2xlarge-[1-2]',
        },
      ],
      teams: [
        {team: 'teama', limit: 1, running: 0, pending: 0, borrowed: 0},
        {team: 'teamb', limit: 0, running: 0, pending: 0, borrowed: 0},
      ],
    },
  ],
  teams: ['teama', 'teamb'],
  unmanaged_queues: [],
  partitions: [
    {
      partition: 'build',
      nodes: [
        {state: 'idle~', count: 2, reason: null, nodes: 'build-dy-m7i-[1-2]'},
      ],
    },
  ],
}
