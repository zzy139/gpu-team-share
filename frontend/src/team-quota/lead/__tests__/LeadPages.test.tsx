import {render, waitFor} from '@testing-library/react'
import {clearAllState, setState} from '../../../store'
import {
  GetLeadQuota,
  ListLeadJobs,
  ListTeamClusters,
  ListTeams,
} from '../../api'
import {
  catalog,
  leadIdentity,
  leadTeams,
  multiTypeQueue,
  TestProviders,
} from '../../test-utils/fixtures'
import {Route, Routes} from 'react-router-dom'
import MyTeamPage from '../../member/MyTeamPage'
jest.mock('../../api', () => ({
  ...(jest.requireActual('../../api') as any),
  ListTeamClusters: jest.fn(),
  ListTeams: jest.fn(),
  ListLeadJobs: jest.fn(),
  GetLeadQuota: jest.fn(),
  GetCapacity: jest.fn().mockResolvedValue({
    types: [],
    teams: ['teama'],
    unmanaged_queues: [],
  }),
}))
// The lead's 我的团队 is the team page; the old team-jobs and quota-holders pages are its 作业 and 成员 tabs.
function renderTab(tab: string) {
  return render(
    <TestProviders path={`/my-team/${tab}`}>
      <Routes>
        <Route path="/my-team/:tab" element={<MyTeamPage />} />
      </Routes>
    </TestProviders>,
  )
}
const cluster = {clusterName: 'hpc-poc', region: 'us-west-2'}
const leadJobs = {
  team: 'teama',
  jobs: [
    {
      job_id: '77',
      user: 'alice',
      name: 'fold',
      partition: 'g6e-xlarge',
      instance_type: 'g6e.xlarge',
      state: 'PENDING',
      reason: 'QOSGrpNodeLimit',
      reason_text:
        'g6e.xlarge quota full (2/2). Try g6e.2xlarge (1 remaining) or tick borrow',
      submit: '2026-11-04T01:00:00',
      start: null,
      time_limit: '1:00:00',
      elapsed: '0:00',
      nodes: 1,
      qos: 'teama-g6e-xlarge',
    },
  ],
}
const leadQuota = {
  team: 'teama',
  types: [
    {
      instance_type: 'g6e.xlarge',
      partition: 'g6e-xlarge',
      gpus: 1,
      limit: 2,
      running: 2,
      pending: 1,
      borrowed: 0,
      holders: [
        {user: 'alice', running: 1, pending: 1},
        {user: 'bob', running: 1, pending: 0},
      ],
    },
    {
      instance_type: catalog[1].instance_type,
      partition: catalog[1].partition,
      gpus: 1,
      limit: 0,
      running: 0,
      pending: 0,
      borrowed: 0,
      holders: [],
    },
  ],
  unmanaged_queues: [multiTypeQueue],
  budget: {
    team: 'teama',
    month: '2026-11',
    budget_usd: 500,
    spent_usd: 520,
    percent: 104,
    level: 'exceeded' as const,
    updated_at: null,
  },
}
describe('team-lead pages', () => {
  beforeEach(() => {
    clearAllState()
    jest.clearAllMocks()
    setState(['identity'], leadIdentity)
    ;(ListTeamClusters as jest.Mock).mockResolvedValue({
      clusters: [{name: 'hpc-poc', region: 'us-west-2'}],
    })
    ;(ListTeams as jest.Mock).mockResolvedValue(leadTeams)
    ;(ListLeadJobs as jest.Mock).mockResolvedValue(leadJobs)
    ;(GetLeadQuota as jest.Mock).mockResolvedValue(leadQuota)
  })
  it('shows the jobs of the own team as metadata with the plain pending reason', async () => {
    const screen = renderTab('jobs')
    await waitFor(() =>
      expect(ListLeadJobs).toHaveBeenCalledWith(
        {...cluster, team: 'teama'},
        'teama',
      ),
    )
    await waitFor(() => expect(screen.getByText('fold')).toBeTruthy())
    expect(screen.getByText('团队 teama 的作业')).toBeTruthy()
    expect(screen.getAllByText('alice').length).toBeGreaterThan(0)
    expect(screen.getByText(/quota full \(2\/2\)/)).toBeTruthy()
    expect(screen.getByText('teama-g6e-xlarge')).toBeTruthy()
  })
  it('shows who holds the machines per instance type, the budget banner and the multi-type alert', async () => {
    const screen = renderTab('members')
    await waitFor(() =>
      expect(GetLeadQuota).toHaveBeenCalledWith(
        {...cluster, team: 'teama'},
        'teama',
      ),
    )
    await waitFor(() =>
      expect(
        screen.getByText('alice（运行 1，排队 1）, bob（运行 1，排队 0）'),
      ).toBeTruthy(),
    )
    expect(screen.getByText('团队 teama 的配额使用情况')).toBeTruthy()
    expect(screen.getByText('不可用')).toBeTruthy()
    expect(screen.getByText('团队 teama 已超出月度预算')).toBeTruthy()
    expect(screen.getByText(/队列 mixed 包含多种机型/)).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/billing|points/i)
  })
})
