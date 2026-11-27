// Every team-quota page, rendered with fixtures, shows no billing or points wording (v2 counts machines per
// instance type and cost in USD only).
import {render, waitFor} from '@testing-library/react'
import React from 'react'
import {Route, Routes} from 'react-router-dom'
import {clearAllState, setState} from '../../store'
import * as teamApi from '../api'
import AuditPage from '../AuditPage'
import CapacityPage from '../CapacityPage'
import * as memberApi from '../member/api'
import EnvsPage, {LeadEnvsPage} from '../member/EnvsPage'
import JobDetailPage from '../member/JobDetailPage'
import MyJobsPage from '../member/MyJobsPage'
import MyTeamPage from '../member/MyTeamPage'
import SubmitJobPage from '../member/SubmitJobPage'
import TemplatesPage from '../member/TemplatesPage'
import {envList} from '../member/test-utils/envs'
import {me, memberIdentity, options} from '../member/test-utils/fixtures'
import {TeamDetailPage} from '../TeamDetail'
import TeamsPage from '../TeamsPage'
import TeamUsagePage from '../TeamUsagePage'
import {
  adminIdentity,
  adminTeams,
  catalog,
  multiTypeQueue,
  teama,
  TestProviders,
  usage,
} from '../test-utils/fixtures'
jest.mock('../api', () => {
  const actual = jest.requireActual('../api') as any
  const mocked: Record<string, jest.Mock> = {}
  Object.keys(actual).forEach(key => {
    if (/^[A-Z]/.test(key)) mocked[key] = jest.fn()
  })
  return {...actual, ...mocked}
})
jest.mock('../member/api', () => {
  const actual = jest.requireActual('../member/api') as any
  const mocked: Record<string, jest.Mock> = {}
  Object.keys(actual).forEach(key => {
    if (/^[A-Z]/.test(key)) mocked[key] = jest.fn()
  })
  return {...actual, ...mocked}
})
const resolve = (fn: unknown, value: unknown) =>
  (fn as jest.Mock).mockResolvedValue(value)
const job = {
  job_id: '12',
  name: 'train',
  user: 'carol',
  account: 'teama',
  partition: 'g6e-xlarge',
  instance_type: 'g6e.xlarge',
  state: 'PENDING',
  reason: 'QOSGrpNodeLimit',
  reason_text: 'g6e.xlarge quota full (2/2)',
  submit: null,
  start: null,
  end: null,
  time_limit: '1:00:00',
  elapsed: '0:00',
  elapsed_seconds: 0,
  exit_code: null,
  alloc_tres: null,
  gres: 'gres/gpu:1',
  cpus: '4',
  memory: '16G',
  qos: 'teama-g6e-xlarge',
  nodes: 1,
  workdir: '/efs/teama',
  stdout_path: '/efs/teama/slurm-12.out',
  stderr_path: '/efs/teama/slurm-12.out',
  team_root: '/efs/teama',
}
function mockAll() {
  resolve(teamApi.ListTeamClusters, {
    clusters: [{name: 'hpc-poc', region: 'us-west-2'}],
  })
  resolve(teamApi.ListTeams, {
    ...adminTeams,
    unmanaged_queues: [multiTypeQueue],
  })
  resolve(teamApi.ListLifecycleTeams, {
    configured: true,
    records: [],
    email_domains: ['example.com'],
  })
  resolve(teamApi.GetCatalog, {types: catalog, unmanaged_queues: []})
  resolve(teamApi.GetCapacity, {
    types: catalog.map(entry => ({
      ...entry,
      status: 'startable',
      no_capacity: false,
      node_total: 4,
      nodes: [{state: 'idle~', count: 4, reason: null, nodes: null}],
      teams: [{team: 'teama', limit: 2, running: 1, pending: 0, borrowed: 0}],
    })),
    teams: ['teama'],
    unmanaged_queues: [],
    partitions: [],
  })
  resolve(teamApi.GetTeamUsage, usage)
  resolve(teamApi.GetTeamTrend, {
    months: [{month: '2026-11', teams: []}],
    billed_available: true,
    sources: usage.sources,
    notes: [],
  })
  resolve(teamApi.ListAudit, {
    months: ['2026-11'],
    entries: [],
    truncated: false,
  })
  resolve(teamApi.ListLeadJobs, {team: 'teama', jobs: [job]})
  resolve(teamApi.GetLeadQuota, {
    team: 'teama',
    types: [],
    unmanaged_queues: [],
    budget: null,
  })
  resolve(memberApi.GetMemberMe, me)
  resolve(memberApi.GetMemberOptions, options)
  resolve(memberApi.ListMemberJobs, {jobs: [job]})
  resolve(memberApi.ListMemberHistory, {jobs: []})
  resolve(memberApi.GetMemberJob, job)
  resolve(memberApi.ListMemberDir, {
    path: '/efs/teama',
    truncated: false,
    entries: [],
  })
  resolve(memberApi.ReadMemberFile, {
    path: '/efs/teama/slurm-12.out',
    size: 0,
    offset: 0,
    length: 0,
    content_b64: '',
    truncated: false,
    limit: 0,
  })
  const {members, ...quota} = teama
  resolve(memberApi.GetMemberTeam, {
    team: 'teama',
    quota,
    catalog,
    usage: usage.teams[0],
    mine: {...usage.by_member![0]},
    period: usage.period,
    billed_available: true,
    sources: usage.sources,
    notes: [],
    budget: null,
  })
  resolve(memberApi.GetMemberBudget, {team: 'teama', budget: null})
  resolve(memberApi.ListTemplates, {
    templates: [
      {
        id: 't1',
        name: 'nightly',
        job: {instance_type: 'g6e.xlarge', nodes: 1, time_minutes: 60},
        created_at: null,
        updated_at: null,
      },
    ],
    max: 50,
  })
  resolve(memberApi.ListNotifications, {items: [], unread: 0})
  resolve(memberApi.ListEnvs, envList)
}
const teamPage = () => (
  <Routes>
    <Route path="/teams/:team/:tab" element={<TeamDetailPage />} />
  </Routes>
)
const pages: [
  string,
  'admin' | 'member',
  () => React.ReactElement,
  string,
  string,
][] = [
  ['Teams', 'admin', () => <TeamsPage />, 'teamb', '/teams'],
  ['Cost overview', 'admin', () => <TeamUsagePage />, 'alice', '/team-usage'],
  ['Audit log', 'admin', () => <AuditPage />, '没有记录', '/audit'],
  [
    'Instance types and nodes',
    'admin',
    () => <CapacityPage />,
    '各团队配额和用量',
    '/capacity',
  ],
  [
    'Team overview',
    'admin',
    () => teamPage(),
    '需启动约5分钟',
    '/teams/teama/overview',
  ],
  [
    'Team members',
    'admin',
    () => teamPage(),
    '团队 teama 的配额使用情况',
    '/teams/teama/members',
  ],
  ['Team jobs', 'admin', () => teamPage(), 'train', '/teams/teama/jobs'],
  ['Team cost', 'admin', () => teamPage(), 'alice', '/teams/teama/cost'],
  [
    'Team environments',
    'admin',
    () => teamPage(),
    'alphafold',
    '/teams/teama/envs',
  ],
  ['Lead environments', 'admin', () => <LeadEnvsPage />, 'alphafold', '/envs'],
  ['My jobs', 'member', () => <MyJobsPage />, 'train', '/my-jobs'],
  ['Submit job', 'member', () => <SubmitJobPage />, 'g6e.12xlarge', '/submit'],
  ['My team', 'member', () => <MyTeamPage />, '机型与配额', '/my-team'],
  ['Environments', 'member', () => <EnvsPage />, 'alphafold', '/envs'],
  ['Job templates', 'member', () => <TemplatesPage />, 'nightly', '/templates'],
  [
    'Job detail',
    'member',
    () => (
      <Routes>
        <Route path="/my-jobs/:jobId" element={<JobDetailPage />} />
      </Routes>
    ),
    'train',
    '/my-jobs/12',
  ],
]
describe('no billing wording', () => {
  beforeEach(() => {
    clearAllState()
    mockAll()
  })
  it.each(pages)('%s', async (_name, role, page, marker, path) => {
    setState(['identity'], role === 'admin' ? adminIdentity : memberIdentity)
    const screen = render(<TestProviders path={path}>{page()}</TestProviders>)
    await waitFor(() =>
      expect(screen.getAllByText(marker).length).toBeGreaterThan(0),
    )
    expect(document.body.textContent).not.toMatch(/billing|points|计费|积分/i)
  })
})
