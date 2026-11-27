import {fireEvent, render, waitFor} from '@testing-library/react'
import {UpdateTeam} from '../api'
import EditTeamModal from '../EditTeamModal'
import {
  catalog,
  multiTypeQueue,
  teama,
  teamb,
  TestProviders,
} from '../test-utils/fixtures'
jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  UpdateTeam: jest.fn(),
}))
function setValue(input: HTMLElement, value: string) {
  fireEvent.change(input, {target: {value}})
}
const target = {clusterName: 'hpc-poc', region: 'us-west-2'}
function renderModal(props: Record<string, unknown> = {}) {
  const onDismiss = jest.fn()
  const screen = render(
    <TestProviders>
      <EditTeamModal
        team={teamb}
        target={target}
        catalog={catalog}
        visible={true}
        onDismiss={onDismiss}
        {...props}
      />
    </TestProviders>,
  )
  return {screen, onDismiss}
}
describe('EditTeamModal', () => {
  beforeEach(() => {
    ;(UpdateTeam as jest.Mock).mockReset()
    ;(UpdateTeam as jest.Mock).mockResolvedValue({
      team: 'teamb',
      result: 'updated',
    })
  })
  it('pre-fills fairshare, machine counts and budget', () => {
    const {screen} = renderModal({team: teama, managed: true})
    const value = (label: string) =>
      (screen.getByLabelText(label) as HTMLInputElement).value
    expect(value('公平份额')).toBe('60')
    expect(value('g6e.xlarge 的机器数')).toBe('2')
    expect(value('g6e.2xlarge 的机器数')).toBe('1')
    expect(value('月度预算（美元，可选）')).toBe('500')
    expect(value('环境存储上限（GB，可选）')).toBe('200')
    expect(document.body.textContent).not.toMatch(/billing|points/i)
  })
  it('blocks submit when a machine count is out of range', () => {
    const {screen} = renderModal()
    setValue(screen.getByLabelText('g6e.xlarge 的机器数'), '1001')
    fireEvent.click(screen.getByRole('button', {name: '保存'}))
    expect(UpdateTeam).not.toHaveBeenCalled()
    expect(screen.getByText('请输入 0 到 1000 之间的值。')).toBeTruthy()
  })
  it('blocks submit when fairshare is not a whole number', () => {
    const {screen} = renderModal()
    setValue(screen.getByLabelText('公平份额'), '1.5')
    fireEvent.click(screen.getByRole('button', {name: '保存'}))
    expect(UpdateTeam).not.toHaveBeenCalled()
  })
  it('sends every catalog type (0 removes one) and clears an empty budget', async () => {
    const {screen, onDismiss} = renderModal()
    setValue(screen.getByLabelText('g6e.xlarge 的机器数'), '')
    setValue(screen.getByLabelText('g6e.2xlarge 的机器数'), '3')
    fireEvent.click(screen.getByRole('button', {name: '保存'}))
    await waitFor(() => expect(onDismiss).toHaveBeenCalled())
    expect(UpdateTeam).toHaveBeenCalledWith(target, 'teamb', {
      fairshare: 40,
      budget_usd: null,
      type_limits: {'g6e.xlarge': 0, 'g6e.2xlarge': 3},
    })
  })
  it('shows the share of the team among all teams while fairshare is edited', () => {
    // teamb 40 among teama 60.
    const {screen} = renderModal({teams: [teama, teamb]})
    fireEvent.click(screen.getByRole('button', {name: '高级选项'}))
    expect(screen.getByText('约占所有团队公平份额的 40.0%。')).toBeTruthy()
    setValue(screen.getByLabelText('公平份额'), '140')
    expect(screen.getByText('约占所有团队公平份额的 70.0%。')).toBeTruthy()
  })
  it('shows the multi-type queue alert', () => {
    const {screen} = renderModal({unmanagedQueues: [multiTypeQueue]})
    expect(screen.getByText('不能按机型设置配额的队列')).toBeTruthy()
  })
})
