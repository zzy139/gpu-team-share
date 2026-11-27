import {render, waitFor} from '@testing-library/react'
import {clearAllState, setState} from '../../store'
import {GetCapacity, ListTeamClusters} from '../api'
import CapacityPage from '../CapacityPage'
import {adminIdentity, capacity, TestProviders} from '../test-utils/fixtures'
jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  ListTeamClusters: jest.fn(),
  GetCapacity: jest.fn(),
}))
function cells(row: Element) {
  return Array.from(row.querySelectorAll('td')).map(cell => cell.textContent)
}
describe('CapacityPage', () => {
  beforeEach(() => {
    clearAllState()
    jest.clearAllMocks()
    setState(['identity'], adminIdentity)
    ;(ListTeamClusters as jest.Mock).mockResolvedValue({
      clusters: [{name: 'hpc-poc', region: 'us-west-2'}],
    })
    ;(GetCapacity as jest.Mock).mockResolvedValue(capacity)
  })
  it('shows every instance type with specs, status and the quota of every team', async () => {
    const screen = render(
      <TestProviders>
        <CapacityPage />
      </TestProviders>,
    )
    await waitFor(() =>
      expect(screen.getByText('各团队配额和用量')).toBeTruthy(),
    )
    await waitFor(() => expect(screen.getByText('有空闲')).toBeTruthy())
    // All teams: the admin call has no team filter.
    expect(GetCapacity).toHaveBeenCalledWith(
      {clusterName: 'hpc-poc', region: 'us-west-2'},
      undefined,
    )
    const [xlarge, xxlarge] = screen
      .getAllByRole('row')
      .filter(row => row.querySelector('td'))
    expect(cells(xlarge)).toEqual([
      'g6e.xlarge',
      '1× NVIDIA L40S 48 GB · 4 vCPU · 32 GiB',
      '有空闲',
      '4',
      '1 / 2（排队 1，借用 0）',
      '0 / 4',
    ])
    expect(cells(xxlarge)).toEqual([
      'g6e.2xlarge',
      '1× NVIDIA L40S 48 GB · 8 vCPU · 64 GiB',
      '暂无容量',
      '2',
      '0 / 1',
      '不可用',
    ])
  })
  it('lists the node states like sinfo, including queues outside the catalog', async () => {
    const screen = render(
      <TestProviders>
        <CapacityPage />
      </TestProviders>,
    )
    // Table title and column header.
    await waitFor(() => expect(screen.getAllByText('节点状态').length).toBe(2))
    // g6e.xlarge and the build queue both have powered-off idle nodes.
    await waitFor(() =>
      expect(screen.getAllByText('idle~（空闲 / 已关机）').length).toBe(2),
    )
    expect(screen.getByText('down~（不可用 / 已关机）')).toBeTruthy()
    expect(
      screen.getByText(
        '(Code:InsufficientInstanceCapacity)Failure when resuming nodes',
      ),
    ).toBeTruthy()
    expect(screen.getByText('g6e-xlarge-dy-g6e-xlarge-[2-4]')).toBeTruthy()
    expect(screen.getByText('build')).toBeTruthy()
    expect(screen.getByText('其他队列')).toBeTruthy()
  })
})
