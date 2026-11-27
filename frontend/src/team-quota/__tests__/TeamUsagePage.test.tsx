import {render, waitFor} from '@testing-library/react'
import {clearAllState, setState} from '../../store'
import {GetTeamTrend, GetTeamUsage, ListTeamClusters} from '../api'
import TeamUsagePage from '../TeamUsagePage'
import {
  adminIdentity,
  leadIdentity,
  TestProviders,
  usage,
} from '../test-utils/fixtures'

jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  ListTeamClusters: jest.fn(),
  GetTeamUsage: jest.fn(),
  GetTeamTrend: jest.fn(),
}))

function renderPage() {
  return render(
    <TestProviders>
      <TeamUsagePage />
    </TestProviders>,
  )
}

describe('TeamUsagePage', () => {
  beforeEach(() => {
    clearAllState()
    ;(ListTeamClusters as jest.Mock).mockResolvedValue({
      clusters: [{name: 'hpc-poc', region: 'us-west-2'}],
    })
    ;(GetTeamTrend as jest.Mock).mockResolvedValue({
      months: [{month: '2026-11', teams: []}],
      billed_available: true,
      sources: usage.sources,
      notes: [],
    })
  })

  it('labels billed and estimate figures and shows shared costs to admins', async () => {
    setState(['identity'], adminIdentity)
    ;(GetTeamUsage as jest.Mock).mockResolvedValue(usage)
    const screen = renderPage()
    await waitFor(() => expect(screen.getAllByText('teama').length).toBe(3))
    expect(screen.getAllByText('账单').length).toBe(3)
    // Team, instance type and member tables each have an estimate column.
    expect(screen.getAllByText('估算').length).toBe(3)
    expect(screen.getAllByText('费用说明').length).toBeGreaterThan(0)
    expect(screen.getByText('$5.56')).toBeTruthy()
    expect(screen.getAllByText('$4.65').length).toBe(2)
    expect(screen.getByText('按机型的用量')).toBeTruthy()
    expect(screen.getByText('按成员的用量')).toBeTruthy()
    expect(screen.getByText('alice')).toBeTruthy()
    expect(screen.getByText('$2.33')).toBeTruthy()
    expect(screen.getByText('集群合计和共享费用')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/billing|points/i)
    expect(GetTeamUsage).toHaveBeenCalledWith(
      {clusterName: 'hpc-poc', region: 'us-west-2'},
      expect.stringMatching(/^\d{4}-\d{2}-01$/),
      expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
      undefined,
    )
  })

  it('hides shared costs and totals from a team lead', async () => {
    setState(['identity'], leadIdentity)
    const {totals, ...leadUsage} = usage
    ;(GetTeamUsage as jest.Mock).mockResolvedValue(leadUsage)
    const screen = renderPage()
    await waitFor(() => expect(screen.getAllByText('teama').length).toBe(3))
    expect(screen.getAllByText('估算').length).toBe(3)
    expect(screen.queryByText('集群合计和共享费用')).toBeNull()
  })

  it('explains billed versus estimated cost with an example', async () => {
    setState(['identity'], adminIdentity)
    ;(GetTeamUsage as jest.Mock).mockResolvedValue(usage)
    const screen = renderPage()
    await waitFor(() => expect(screen.getAllByText('teama').length).toBe(3))
    expect(screen.getByText(/大约延迟 24 小时/)).toBeTruthy()
    expect(screen.getByText(/机器小时 × 该机型的按需单价/)).toBeTruthy()
    expect(screen.getByText(/合计 \$29\.77/)).toBeTruthy()
  })
  it('shows notes from the server', async () => {
    setState(['identity'], adminIdentity)
    ;(GetTeamUsage as jest.Mock).mockResolvedValue({
      ...usage,
      billed_available: false,
      notes: ['billed-unavailable', 'unknown-note'],
    })
    const screen = renderPage()
    await waitFor(() =>
      expect(
        screen.getAllByText(/暂时无法从 Cost Explorer 获取账单费用/).length,
      ).toBeGreaterThan(0),
    )
  })
})
