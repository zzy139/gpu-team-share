import {fireEvent, render, waitFor} from '@testing-library/react'
import {Route, Routes, useLocation} from 'react-router-dom'
import {clearAllState, setState} from '../../store'
import {
  GetCapacity,
  GetLeadQuota,
  GetTeamTrend,
  GetTeamUsage,
  ListLeadJobs,
  ListLifecycleTeams,
  ListTeamClusters,
  ListTeams,
} from '../api'
import {ListEnvs} from '../member/api'
import {envList} from '../member/test-utils/envs'
import {TeamDetailPage} from '../TeamDetail'
import {
  adminIdentity,
  adminTeams,
  capacity,
  TestProviders,
  usage,
} from '../test-utils/fixtures'
jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  ListTeamClusters: jest.fn(),
  ListTeams: jest.fn(),
  ListLifecycleTeams: jest.fn(),
  GetCapacity: jest.fn(),
  ListLeadJobs: jest.fn(),
  GetLeadQuota: jest.fn(),
  GetTeamUsage: jest.fn(),
  GetTeamTrend: jest.fn(),
}))
jest.mock('../member/api', () => ({
  ...(jest.requireActual('../member/api') as any),
  ListEnvs: jest.fn(),
}))
const cluster = {clusterName: 'hpc-poc', region: 'us-west-2'}
function Location() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}
function renderPage(path: string) {
  return render(
    <TestProviders path={path}>
      <Routes>
        <Route path="/teams/:team" element={<TeamDetailPage />} />
        <Route path="/teams/:team/:tab" element={<TeamDetailPage />} />
      </Routes>
      <Location />
    </TestProviders>,
  )
}
describe('TeamDetailPage', () => {
  beforeEach(() => {
    clearAllState()
    jest.clearAllMocks()
    setState(['identity'], adminIdentity)
    ;(ListTeamClusters as jest.Mock).mockResolvedValue({
      clusters: [{name: 'hpc-poc', region: 'us-west-2'}],
    })
    ;(ListTeams as jest.Mock).mockResolvedValue(adminTeams)
    ;(ListLifecycleTeams as jest.Mock).mockResolvedValue({
      configured: true,
      records: [{team: 'teama', state: 'active'}],
      email_domains: ['example.com'],
    })
    ;(GetCapacity as jest.Mock).mockResolvedValue(capacity)
    ;(ListLeadJobs as jest.Mock).mockResolvedValue({team: 'teama', jobs: []})
    ;(GetLeadQuota as jest.Mock).mockResolvedValue({
      team: 'teama',
      types: [],
      unmanaged_queues: [],
      budget: null,
    })
    ;(GetTeamUsage as jest.Mock).mockResolvedValue(usage)
    ;(GetTeamTrend as jest.Mock).mockResolvedValue({
      months: [],
      billed_available: true,
      sources: usage.sources,
      notes: [],
    })
    ;(ListEnvs as jest.Mock).mockResolvedValue(envList)
  })
  it('shows the five tabs and 机型与配额 with specs, the team quota and the capacity status', async () => {
    const screen = renderPage('/teams/teama')
    await waitFor(() =>
      expect(
        screen.getByText('1× NVIDIA L40S 48 GB · 4 vCPU · 32 GiB'),
      ).toBeTruthy(),
    )
    expect(screen.getByText('团队 teama')).toBeTruthy()
    expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual([
      '概览',
      '成员',
      '作业',
      '费用',
      '环境',
    ])
    expect(GetCapacity).toHaveBeenCalledWith(cluster, 'teama')
    expect(screen.getByText('机型与配额')).toBeTruthy()
    expect(screen.getByText('有空闲')).toBeTruthy()
    expect(screen.getByText('暂无容量')).toBeTruthy()
    // teama: 2 machines of g6e.xlarge, 1 running, 1 left.
    const row = screen.getByText('g6e.xlarge').closest('tr')!
    expect(
      Array.from(row.querySelectorAll('td')).map(cell => cell.textContent),
    ).toEqual([
      'g6e.xlarge',
      '1× NVIDIA L40S 48 GB · 4 vCPU · 32 GiB',
      '2',
      '1',
      '1',
      '有空闲',
    ])
  })
  it('lists the members with their state and lets an admin manage them', async () => {
    const screen = renderPage('/teams/teama/members')
    await waitFor(() => expect(screen.getByText('teama 的成员')).toBeTruthy())
    // The member table, and the (closed) members dialog that is rendered hidden.
    expect(screen.getAllByText('alice').length).toBeGreaterThan(0)
    expect(screen.getAllByText('正常').length).toBeGreaterThan(0)
    await waitFor(() =>
      expect(GetLeadQuota).toHaveBeenCalledWith(
        {...cluster, team: 'teama'},
        'teama',
      ),
    )
    fireEvent.click(screen.getByRole('button', {name: '管理成员'}))
    await waitFor(() =>
      expect(screen.getByLabelText('按邮箱添加成员')).toBeTruthy(),
    )
  })
  it('opens the team jobs, the team cost and the team environments as tabs', async () => {
    const screen = renderPage('/teams/teama/jobs')
    await waitFor(() =>
      expect(ListLeadJobs).toHaveBeenCalledWith(
        {...cluster, team: 'teama'},
        'teama',
      ),
    )
    fireEvent.click(screen.getByRole('tab', {name: '费用'}))
    await waitFor(() =>
      expect(screen.getByTestId('location').textContent).toBe(
        '/teams/teama/cost',
      ),
    )
    await waitFor(() =>
      expect(GetTeamUsage).toHaveBeenCalledWith(
        cluster,
        expect.any(String),
        expect.any(String),
        'teama',
      ),
    )
    expect(GetTeamTrend).toHaveBeenCalledWith(cluster, 6, 'teama')
    // The cost tab of one team has no cluster totals.
    expect(screen.queryByText('集群合计和共享费用')).toBeNull()
    expect(screen.getAllByText('费用说明').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('tab', {name: '环境'}))
    await waitFor(() =>
      expect(ListEnvs).toHaveBeenCalledWith({...cluster, team: 'teama'}),
    )
    fireEvent.click(screen.getByRole('tab', {name: '概览'}))
    await waitFor(() =>
      expect(screen.getByTestId('location').textContent).toBe('/teams/teama'),
    )
  })
  it('warns when the team is not on the cluster', async () => {
    const screen = renderPage('/teams/nosuch')
    await waitFor(() =>
      expect(screen.getByText('此集群上没有团队 nosuch。')).toBeTruthy(),
    )
  })
})
