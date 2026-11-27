import {fireEvent, render, waitFor} from '@testing-library/react'
import {Route, Routes} from 'react-router-dom'
import {clearAllState, setState} from '../../../store'
import {GetCapacity, ListTeamClusters, ListTeams} from '../../api'
import {
  catalog,
  leadIdentity,
  leadTeams,
  teama,
  TestProviders,
  usage,
} from '../../test-utils/fixtures'
import {
  GetMemberBudget,
  GetMemberMe,
  GetMemberOptions,
  GetMemberTeam,
} from '../api'
import MyTeamPage from '../MyTeamPage'
import {me, memberIdentity, memberTarget, options} from '../test-utils/fixtures'
jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  GetMemberBudget: jest.fn(),
  ListNotifications: jest.fn().mockResolvedValue({items: [], unread: 0}),
  GetMemberMe: jest.fn(),
  GetMemberTeam: jest.fn(),
  GetMemberOptions: jest.fn(),
}))
jest.mock('../../api', () => ({
  ...(jest.requireActual('../../api') as any),
  ListTeamClusters: jest.fn(),
  ListTeams: jest.fn(),
  GetCapacity: jest.fn(),
}))
function renderPage(path = '/my-team') {
  return render(
    <TestProviders path={path}>
      <Routes>
        <Route path="/my-team" element={<MyTeamPage />} />
        <Route path="/my-team/:tab" element={<MyTeamPage />} />
      </Routes>
    </TestProviders>,
  )
}
describe('MyTeamPage', () => {
  beforeEach(() => {
    clearAllState()
    jest.clearAllMocks()
    setState(['identity'], memberIdentity)
    ;(GetMemberMe as jest.Mock).mockResolvedValue(me)
    ;(GetMemberOptions as jest.Mock).mockResolvedValue({
      ...options,
      // the two types the team has quota for
      types: options.types.slice(0, 2).map((type, index) => ({
        ...type,
        gpu_model: 'NVIDIA L40S',
        gpu_memory_gb: 48,
        status: index ? 'no_capacity' : 'available',
      })),
    })
    ;(GetMemberBudget as jest.Mock).mockResolvedValue({
      team: 'teama',
      budget: {
        team: 'teama',
        month: '2026-11',
        budget_usd: 500,
        spent_usd: 420,
        percent: 84,
        level: 'warning',
        updated_at: '2026-11-04T01:00:00Z',
      },
    })
    const {members, ...quota} = teama
    ;(GetMemberTeam as jest.Mock).mockResolvedValue({
      team: 'teama',
      quota,
      catalog,
      usage: {...usage.teams[0], instance_hours: 12.5, estimate_cost: 10.06},
      mine: {
        user: 'carol',
        instance_hours: 2.25,
        gpu_hours: 2.25,
        estimate_cost: 1.81,
        unpriced_instance_hours: 0,
        by_type: [],
      },
      period: {start: '2026-11-01', end: '2026-11-03'},
      billed_available: true,
      sources: {billed: 'cost-explorer', estimate: 'pricing-api'},
      notes: [],
      budget: null,
    })
  })
  it('shows a member 概览 with specs, quota, use and capacity status, and the budget banner', async () => {
    const screen = renderPage()
    await waitFor(() => expect(screen.getByText('g6e.12xlarge')).toBeTruthy())
    expect(GetMemberOptions).toHaveBeenCalledWith(memberTarget)
    expect(screen.getByText('机型与配额')).toBeTruthy()
    expect(
      screen.getByText('4× NVIDIA L40S 48 GB · 48 vCPU · 384 GiB'),
    ).toBeTruthy()
    expect(screen.getByText('有空闲')).toBeTruthy()
    expect(screen.getByText('暂无容量')).toBeTruthy()
    // Only 概览 and 费用 for a member; no team management tabs.
    expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual([
      '概览',
      '费用',
    ])
    await waitFor(() =>
      expect(screen.getByText('团队 teama 已用掉月度预算的 80%')).toBeTruthy(),
    )
    expect(GetMemberTeam).not.toHaveBeenCalled()
  })
  it('shows own versus team hours and cost on the 费用 tab', async () => {
    const screen = renderPage('/my-team/cost')
    await waitFor(() => expect(screen.getByText('我（carol）')).toBeTruthy())
    expect(GetMemberTeam).toHaveBeenCalledWith(
      memberTarget,
      expect.any(String),
      expect.any(String),
    )
    expect(screen.getAllByText('2.25').length).toBe(2)
    expect(screen.getByText('12.50')).toBeTruthy()
    expect(screen.getByText('$1.81')).toBeTruthy()
    expect(screen.getByText('$10.06')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/billing|points|计费/i)
  })
  it('switches tabs through the URL', async () => {
    const screen = renderPage()
    await waitFor(() => expect(screen.getByText('g6e.12xlarge')).toBeTruthy())
    fireEvent.click(screen.getByRole('tab', {name: '费用'}))
    await waitFor(() => expect(screen.getByText('我（carol）')).toBeTruthy())
  })
  it('gives a team lead the whole team page', async () => {
    setState(['identity'], leadIdentity)
    ;(ListTeamClusters as jest.Mock).mockResolvedValue({
      clusters: [{name: 'hpc-poc', region: 'us-west-2'}],
    })
    ;(ListTeams as jest.Mock).mockResolvedValue(leadTeams)
    ;(GetCapacity as jest.Mock).mockResolvedValue({
      types: [],
      teams: ['teama'],
      unmanaged_queues: [],
    })
    const screen = renderPage()
    await waitFor(() =>
      expect(screen.getAllByText('团队 teama').length).toBeGreaterThan(0),
    )
    expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual([
      '概览',
      '成员',
      '作业',
      '费用',
      '环境',
    ])
    await waitFor(() =>
      expect(GetCapacity).toHaveBeenCalledWith(
        {clusterName: 'hpc-poc', region: 'us-west-2'},
        'teama',
      ),
    )
    expect(GetMemberOptions).not.toHaveBeenCalled()
  })
})
