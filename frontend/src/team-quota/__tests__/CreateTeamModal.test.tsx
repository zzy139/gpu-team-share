import {fireEvent, render, waitFor} from '@testing-library/react'
import {CreateLifecycleTeam, CreateTeam} from '../api'
import CreateTeamModal from '../CreateTeamModal'
import {
  buildQueue,
  catalog,
  multiTypeQueue,
  teama,
  teamb,
  TestProviders,
} from '../test-utils/fixtures'
jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  CreateTeam: jest.fn(),
  CreateLifecycleTeam: jest.fn(),
}))
const target = {clusterName: 'hpc-poc', region: 'us-west-2'}
const lifecycle = {
  configured: true,
  records: [],
  email_domains: ['example.com'],
}
function setValue(input: HTMLElement, value: string) {
  fireEvent.change(input, {target: {value}})
}
function renderModal(props: Record<string, unknown> = {}) {
  const onDismiss = jest.fn()
  const onProvision = jest.fn()
  const screen = render(
    <TestProviders>
      <CreateTeamModal
        target={target}
        catalog={catalog}
        unmanagedQueues={[buildQueue]}
        visible={true}
        onDismiss={onDismiss}
        onProvision={onProvision}
        {...props}
      />
    </TestProviders>,
  )
  const name = screen.getByLabelText('团队名称')
  return {screen, name, onDismiss, onProvision}
}
describe('CreateTeamModal', () => {
  beforeEach(() => {
    ;(CreateTeam as jest.Mock).mockReset().mockResolvedValue({team: 'qatest'})
    ;(CreateLifecycleTeam as jest.Mock)
      .mockReset()
      .mockResolvedValue({team: 'teamc', state: 'provisioning', steps: []})
  })
  it('lists every catalog instance type with a machine count and no billing field', () => {
    const {screen} = renderModal()
    expect(screen.getByLabelText('g6e.xlarge 的机器数')).toBeTruthy()
    expect(screen.getByLabelText('g6e.2xlarge 的机器数')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/billing|points/i)
  })
  it('shows the specs of every instance type', () => {
    const {screen} = renderModal()
    expect(screen.getByText('1× GPU · 4 vCPU · 32 GiB')).toBeTruthy()
    expect(screen.getByText('1× GPU · 8 vCPU · 64 GiB')).toBeTruthy()
  })
  it('keeps fairshare under 高级选项 with the default 50 and shows the live share', () => {
    // Existing teams: 60 + 40.
    const {screen} = renderModal({teams: [teama, teamb]})
    const advanced = screen.getByRole('button', {name: '高级选项'})
    expect(advanced.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(advanced)
    const fairshare = screen.getByLabelText('公平份额') as HTMLInputElement
    expect(fairshare.value).toBe('50')
    expect(screen.getByText('约占所有团队公平份额的 33.3%。')).toBeTruthy()
    setValue(fairshare, '100')
    expect(screen.getByText('约占所有团队公平份额的 50.0%。')).toBeTruthy()
  })
  it('opens 高级选项 when fairshare is not valid', () => {
    const {screen, name} = renderModal()
    setValue(name, 'qatest')
    setValue(screen.getByLabelText('g6e.xlarge 的机器数'), '1')
    setValue(screen.getByLabelText('公平份额'), '0')
    fireEvent.click(screen.getByRole('button', {name: '创建'}))
    expect(CreateTeam).not.toHaveBeenCalled()
    expect(
      screen
        .getByRole('button', {name: '高级选项'})
        .getAttribute('aria-expanded'),
    ).toBe('true')
    expect(screen.getByText('请输入 1 到 10000 之间的值。')).toBeTruthy()
  })
  it('creates a Slurm-only team with per-type counts and an optional budget', async () => {
    const {screen, name, onDismiss} = renderModal()
    setValue(name, 'qatest')
    setValue(screen.getByLabelText('g6e.xlarge 的机器数'), '2')
    setValue(screen.getByLabelText('月度预算（美元，可选）'), '500')
    fireEvent.click(screen.getByRole('button', {name: '创建'}))
    await waitFor(() => expect(onDismiss).toHaveBeenCalled())
    expect(CreateTeam).toHaveBeenCalledWith(target, {
      name: 'qatest',
      fairshare: 50,
      type_limits: {'g6e.xlarge': 2},
      budget_usd: 500,
    })
    expect(CreateLifecycleTeam).not.toHaveBeenCalled()
  })
  it('needs at least one allowed instance type', () => {
    const {screen, name} = renderModal()
    setValue(name, 'qatest')
    fireEvent.click(screen.getByRole('button', {name: '创建'}))
    expect(CreateTeam).not.toHaveBeenCalled()
    expect(screen.getByText('至少允许一种机型（机器数大于 0）。')).toBeTruthy()
  })
  it('creates a lifecycle team with email members, env cap and the derived cluster user', async () => {
    const {screen, name, onProvision} = renderModal({lifecycle})
    setValue(name, 'teamc')
    setValue(screen.getByLabelText('g6e.2xlarge 的机器数'), '1')
    setValue(screen.getByLabelText('环境存储上限（GB，可选）'), '200')
    fireEvent.click(screen.getByRole('button', {name: '添加成员'}))
    setValue(screen.getByLabelText('成员 1 邮箱'), 'Carol@example.com')
    expect(screen.getByText('集群用户名：carol')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', {name: '创建'}))
    await waitFor(() => expect(onProvision).toHaveBeenCalledWith('teamc'))
    expect(CreateLifecycleTeam).toHaveBeenCalledWith(target, {
      name: 'teamc',
      fairshare: 50,
      type_limits: {'g6e.2xlarge': 1},
      env_storage_cap_gb: 200,
      members: [{email: 'carol@example.com', link_existing: false}],
    })
    const body = (CreateLifecycleTeam as jest.Mock).mock.calls[0][1]
    expect(JSON.stringify(body)).not.toMatch(/password|billing/i)
    expect(document.querySelector('input[type="password"]')).toBeNull()
  })
  it('shows the member email hint, an inline error without "@" and hides link-existing under Advanced', () => {
    const {screen} = renderModal({lifecycle})
    fireEvent.click(screen.getByRole('button', {name: '添加成员'}))
    expect(
      screen.getByText(
        '完整邮箱，例如 zhangsan@example.com（允许的域名：example.com）',
      ),
    ).toBeTruthy()
    setValue(screen.getByLabelText('成员 1 邮箱'), 'zhangsan')
    expect(
      screen.getByText(
        "请输入包含 '@' 的完整邮箱地址，例如 zhangsan@example.com。",
      ),
    ).toBeTruthy()
    // The member row comes before the team's own 高级选项 (fairshare) at the end of the form.
    const advanced = screen.getAllByRole('button', {name: '高级选项'})[0]
    expect(advanced.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(advanced)
    const checkbox = screen.getByRole('checkbox', {
      name: '沿用 AD 中的现有账号',
    }) as HTMLInputElement
    expect(checkbox.checked).toBe(false)
    expect(
      screen.getByText('仅当 AD 中已有同名旧账号且需要沿用时勾选'),
    ).toBeTruthy()
  })
  it('rejects names with "_" and emails outside the allowed domain', () => {
    const {screen, name} = renderModal({lifecycle})
    setValue(name, 'team_c')
    setValue(screen.getByLabelText('g6e.xlarge 的机器数'), '1')
    fireEvent.click(screen.getByRole('button', {name: '添加成员'}))
    setValue(screen.getByLabelText('成员 1 邮箱'), 'carol@other.com')
    fireEvent.click(screen.getByRole('button', {name: '创建'}))
    expect(CreateLifecycleTeam).not.toHaveBeenCalled()
    expect(screen.getByText('不允许使用此邮箱域名。')).toBeTruthy()
    expect(screen.getByText(/不能有 '_'，最多 32 个字符/)).toBeTruthy()
  })
  it('warns about queues with several instance types', () => {
    const {screen} = renderModal({
      unmanagedQueues: [multiTypeQueue, buildQueue],
    })
    expect(screen.getByText(/队列 mixed 包含多种机型/)).toBeTruthy()
    expect(screen.queryByText(/队列 build/)).toBeNull()
    expect(screen.queryByLabelText('g6.xlarge 的机器数')).toBeNull()
  })
})
