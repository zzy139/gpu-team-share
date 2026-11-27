import {fireEvent, render, waitFor} from '@testing-library/react'
import {clearAllState, setState} from '../../store'
import {OffboardTeamMember, ReenableTeamMember} from '../api'
import MembersModal from '../MembersModal'
import {adminIdentity, teama, TestProviders} from '../test-utils/fixtures'
jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  OffboardTeamMember: jest.fn(),
  ReenableTeamMember: jest.fn(),
}))
const target = {clusterName: 'hpc-poc', region: 'us-west-2'}
const steps = [
  {id: 'cognito', status: 'done'},
  {id: 'ad', status: 'done'},
  {id: 'slurm', status: 'done'},
  {id: 'envs', status: 'done'},
]
function renderModal(team = teama, managed = true) {
  return render(
    <TestProviders>
      <MembersModal
        team={team}
        target={target}
        visible
        onDismiss={() => {}}
        managed={managed}
        emailDomains={['example.com']}
      />
    </TestProviders>,
  )
}
describe('MembersModal offboarding', () => {
  beforeEach(() => {
    clearAllState()
    jest.clearAllMocks()
    setState(['identity'], adminIdentity)
  })
  it('offboards a member and cancels the running jobs when asked', async () => {
    ;(OffboardTeamMember as jest.Mock).mockResolvedValue({
      team: 'teama',
      user: 'alice',
      state: 'offboarded',
      steps,
    })
    const screen = renderModal()
    expect(screen.getByText('正常')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', {name: '停用 alice'}))
    expect(screen.getByText(/alice 将无法再登录/)).toBeTruthy()
    fireEvent.click(screen.getByLabelText('取消正在运行的作业'))
    fireEvent.click(screen.getByRole('button', {name: '停用'}))
    await waitFor(() =>
      expect(OffboardTeamMember).toHaveBeenCalledWith(
        target,
        'teama',
        'alice',
        'cancel',
      ),
    )
    await waitFor(() => expect(screen.getByText('已停用 alice')).toBeTruthy())
    expect(screen.getByText(/环境归属: 已完成/)).toBeTruthy()
  })
  it('lets running jobs finish by default', async () => {
    ;(OffboardTeamMember as jest.Mock).mockResolvedValue({
      team: 'teama',
      user: 'alice',
      state: 'finishing',
      steps,
    })
    const screen = renderModal()
    fireEvent.click(screen.getByRole('button', {name: '停用 alice'}))
    fireEvent.click(screen.getByRole('button', {name: '停用'}))
    await waitFor(() =>
      expect(OffboardTeamMember).toHaveBeenCalledWith(
        target,
        'teama',
        'alice',
        'finish',
      ),
    )
  })
  it('re-enables an offboarded member', async () => {
    ;(ReenableTeamMember as jest.Mock).mockResolvedValue({
      team: 'teama',
      user: 'alice',
      state: 'active',
      steps: steps.slice(0, 3),
    })
    const screen = renderModal({...teama, offboarded: {alice: 'offboarded'}})
    expect(screen.getByText('已停用')).toBeTruthy()
    expect(screen.queryByRole('button', {name: '停用 alice'})).toBeNull()
    fireEvent.click(screen.getByRole('button', {name: '重新启用 alice'}))
    await waitFor(() =>
      expect(ReenableTeamMember).toHaveBeenCalledWith(target, 'teama', 'alice'),
    )
    await waitFor(() =>
      expect(screen.getByText('已重新启用 alice')).toBeTruthy(),
    )
  })
  it('still lists a member whose association the scheduler already removed', () => {
    const screen = renderModal({
      ...teama,
      offboarded: {zed: 'offboarded'},
    })
    expect(screen.getByText('zed')).toBeTruthy()
    expect(screen.getByRole('button', {name: '重新启用 zed'})).toBeTruthy()
    expect(screen.queryByRole('button', {name: '移除 zed'})).toBeNull()
  })
  it('offers no offboarding for Slurm-only teams', () => {
    const screen = renderModal(teama, false)
    expect(screen.queryByRole('button', {name: '停用 alice'})).toBeNull()
    expect(screen.getByRole('button', {name: '移除 alice'})).toBeTruthy()
  })
})
