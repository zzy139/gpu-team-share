import {fireEvent, render, waitFor} from '@testing-library/react'
import {clearAllState, setState} from '../../store'
import {
  DeleteTeam,
  ListLifecycleTeams,
  ListTeamClusters,
  ListTeams,
} from '../api'
import TeamsPage from '../TeamsPage'
import {
  adminIdentity,
  adminTeams,
  buildQueue,
  multiTypeQueue,
  leadIdentity,
  leadTeams,
  TestProviders,
} from '../test-utils/fixtures'

jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  ListTeamClusters: jest.fn(),
  ListTeams: jest.fn(),
  ListLifecycleTeams: jest.fn(),
  DeleteTeam: jest.fn(),
}))

const ACTIONS = ['创建团队', '修改配额', '管理成员', '删除', '刷新']

describe('TeamsPage', () => {
  beforeEach(() => {
    clearAllState()
    ;(ListTeamClusters as jest.Mock).mockResolvedValue({
      clusters: [{name: 'hpc-poc', region: 'us-west-2'}],
    })
    ;(ListLifecycleTeams as jest.Mock).mockReset().mockResolvedValue({
      configured: false,
      records: [],
      email_domains: [],
    })
  })

  it('shows all teams and the admin actions to an admin', async () => {
    setState(['identity'], adminIdentity)
    ;(ListTeams as jest.Mock).mockResolvedValue(adminTeams)
    const screen = render(
      <TestProviders>
        <TeamsPage />
      </TestProviders>,
    )
    await waitFor(() => expect(screen.getByText('teamb')).toBeTruthy())
    expect(screen.getByText('teama')).toBeTruthy()
    ACTIONS.forEach(action =>
      expect(
        screen.getAllByRole('button', {name: action}).length,
      ).toBeGreaterThan(0),
    )
    expect(ListTeams).toHaveBeenCalledWith({
      clusterName: 'hpc-poc',
      region: 'us-west-2',
    })
    expect(screen.queryByText(/此页面只读/)).toBeNull()
  })
  it('shows machines in use per instance type and no billing text', async () => {
    setState(['identity'], adminIdentity)
    ;(ListTeams as jest.Mock).mockResolvedValue(adminTeams)
    const screen = render(
      <TestProviders>
        <TeamsPage />
      </TestProviders>,
    )
    await waitFor(() => expect(screen.getByText('teamb')).toBeTruthy())
    expect(screen.getAllByText('g6e.xlarge').length).toBeGreaterThan(0)
    expect(screen.getByText('1 / 2')).toBeTruthy()
    expect(screen.getByText('0 / 4')).toBeTruthy()
    expect(screen.getAllByText('不可用').length).toBe(1)
    expect(screen.getByText('$500.00')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/billing|points/i)
  })
  it('links each team to its team page and explains fairshare', async () => {
    setState(['identity'], adminIdentity)
    ;(ListTeams as jest.Mock).mockResolvedValue(adminTeams)
    const screen = render(
      <TestProviders>
        <TeamsPage />
      </TestProviders>,
    )
    await waitFor(() => expect(screen.getByText('teamb')).toBeTruthy())
    expect(screen.getByRole('link', {name: 'teama'}).getAttribute('href')).toBe(
      '/teams/teama',
    )
    expect(screen.getByText('60 (60.0%)')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', {name: '什么是公平份额？'}))
    await waitFor(() =>
      expect(
        screen.getByText(/公平份额决定多个团队的作业同时排队时谁先被调度/),
      ).toBeTruthy(),
    )
    // The instance type columns show the specs.
    expect(
      screen.getAllByText('1× GPU · 4 vCPU · 32 GiB').length,
    ).toBeGreaterThan(0)
  })
  it('shows the multi-type queue alert but not for the build queue', async () => {
    setState(['identity'], adminIdentity)
    ;(ListTeams as jest.Mock).mockResolvedValue({
      ...adminTeams,
      unmanaged_queues: [multiTypeQueue, buildQueue],
    })
    const screen = render(
      <TestProviders>
        <TeamsPage />
      </TestProviders>,
    )
    // On the page and in the (hidden) create and edit modals.
    await waitFor(() =>
      expect(
        screen.getAllByText('不能按机型设置配额的队列').length,
      ).toBeGreaterThan(0),
    )
    expect(
      screen.getAllByText(/队列 mixed 包含多种机型/).length,
    ).toBeGreaterThan(0)
    expect(screen.queryByText(/队列 build/)).toBeNull()
  })

  it('shows a read-only view without actions to a team lead', async () => {
    setState(['identity'], leadIdentity)
    ;(ListTeams as jest.Mock).mockResolvedValue(leadTeams)
    const screen = render(
      <TestProviders>
        <TeamsPage />
      </TestProviders>,
    )
    await waitFor(() => expect(screen.getByText('teama')).toBeTruthy())
    expect(screen.queryByText('teamb')).toBeNull()
    ACTIONS.forEach(action =>
      expect(screen.queryAllByRole('button', {name: action})).toHaveLength(0),
    )
    expect(screen.getByText(/此页面只读/)).toBeTruthy()
  })

  it('never calls the lifecycle endpoints for a team lead', async () => {
    setState(['identity'], leadIdentity)
    ;(ListTeams as jest.Mock).mockResolvedValue(leadTeams)
    const screen = render(
      <TestProviders>
        <TeamsPage />
      </TestProviders>,
    )
    await waitFor(() => expect(screen.getByText('teama')).toBeTruthy())
    expect(ListLifecycleTeams).not.toHaveBeenCalled()
  })

  it('offers to resume a team that is still provisioning', async () => {
    setState(['identity'], adminIdentity)
    ;(ListTeams as jest.Mock).mockResolvedValue(adminTeams)
    ;(ListLifecycleTeams as jest.Mock).mockResolvedValue({
      configured: true,
      email_domains: ['example.com'],
      records: [{team: 'teamc', state: 'provisioning'}],
    })
    const screen = render(
      <TestProviders>
        <TeamsPage />
      </TestProviders>,
    )
    await waitFor(() =>
      expect(screen.getByText('团队 teamc 仍在创建中')).toBeTruthy(),
    )
    expect(screen.getByRole('button', {name: '继续'})).toBeTruthy()
  })

  it('lists the retained resources after a delete', async () => {
    setState(['identity'], adminIdentity)
    ;(ListTeams as jest.Mock).mockResolvedValue(adminTeams)
    ;(DeleteTeam as jest.Mock).mockResolvedValue({
      team: 'teamb',
      result: 'deleted',
      removed: ['slurm-user:bob', 'slurm-account', 'slurm-qos'],
      retained: {
        ad_group: 'teamB',
        team_path: '/efs/teamb',
        ecr_repo: 'hpc-poc-teamb-envs',
        cognito_users: ['bob@example.com'],
      },
    })
    const screen = render(
      <TestProviders>
        <TeamsPage />
      </TestProviders>,
    )
    await waitFor(() => expect(screen.getByText('teamb')).toBeTruthy())
    const radios = screen.getAllByRole('radio')
    fireEvent.click(radios[1])
    const deleteButton = screen.getAllByRole('button', {name: '删除'})[0]
    expect((deleteButton as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(deleteButton)
    await waitFor(() =>
      expect(screen.getByText(/保留：EFS 团队目录/)).toBeTruthy(),
    )
    const confirm = screen.getAllByRole('button', {name: '删除'}).pop()!
    fireEvent.click(confirm)
    await waitFor(() => expect(DeleteTeam).toHaveBeenCalled())
    await waitFor(() =>
      expect(
        screen.getByText('团队 teamb 已删除。以下资源已保留：'),
      ).toBeTruthy(),
    )
    expect(screen.getByText(/团队目录: \/efs\/teamb/)).toBeTruthy()
    expect(screen.getByText(/ECR 仓库: hpc-poc-teamb-envs/)).toBeTruthy()
    expect(screen.getByText(/PCUI 用户: bob@example.com/)).toBeTruthy()
  })
})
