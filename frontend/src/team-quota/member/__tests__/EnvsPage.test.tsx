import wrapper from '@cloudscape-design/components/test-utils/dom'
import {fireEvent, render, waitFor, within} from '@testing-library/react'
import {clearAllState, setState} from '../../../store'
import {ListTeamClusters, ListTeams} from '../../api'
import {leadIdentity, leadTeams, TestProviders} from '../../test-utils/fixtures'
import {
  CreateEnv,
  DeleteEnv,
  GetMemberMe,
  ListEnvs,
  SetEnvVisibility,
} from '../api'
import EnvsPage, {buildEnvRequest, LeadEnvsPage} from '../EnvsPage'
import {envEntry, envList} from '../test-utils/envs'
import {me, memberIdentity, memberTarget} from '../test-utils/fixtures'
jest.mock('../../api', () => ({
  ...(jest.requireActual('../../api') as any),
  ListTeamClusters: jest.fn(),
  ListTeams: jest.fn(),
}))
jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  GetMemberBudget: jest.fn().mockResolvedValue({team: 'teama', budget: null}),
  ListNotifications: jest.fn().mockResolvedValue({items: [], unread: 0}),
  GetMemberMe: jest.fn(),
  ListEnvs: jest.fn(),
  CreateEnv: jest.fn(),
  DeleteEnv: jest.fn(),
  RetryEnv: jest.fn(),
  SetEnvVisibility: jest.fn(),
}))
const t = (key: string) => key
const values = {
  source: 'image' as const,
  name: 'cuda',
  version: '12.4',
  image: 'nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04',
  dockerfile: '',
  condaMode: 'environment_yml' as const,
  condaText: '',
  python: '',
  description: '',
  software: '',
}
function row(screen: any, text: string) {
  return screen.getAllByText(text)[0].closest('tr') as HTMLElement
}
async function renderMember() {
  const screen = render(
    <TestProviders>
      <EnvsPage />
    </TestProviders>,
  )
  await waitFor(() => expect(ListEnvs).toHaveBeenCalledWith(memberTarget))
  await waitFor(() => expect(screen.getByText('alphafold')).toBeTruthy())
  return screen
}
describe('EnvsPage', () => {
  beforeEach(() => {
    clearAllState()
    jest.clearAllMocks()
    ;(GetMemberMe as jest.Mock).mockResolvedValue(me)
    ;(ListEnvs as jest.Mock).mockResolvedValue(envList)
  })
  it('shows one list with the own and the 所有人 environments of other teams, visibility, status, storage and the allowed actions', async () => {
    setState(['identity'], memberIdentity)
    const screen = await renderMember()
    expect(screen.queryByText('团队环境')).toBeNull()
    expect(screen.queryByText('共享环境')).toBeNull()
    expect(screen.queryByText('检测到')).toBeNull()
    for (const header of ['名称', '版本', '团队', '可见范围', '状态', '创建人'])
      expect(screen.getAllByText(header).length).toBeGreaterThan(0)
    expect(within(row(screen, 'alphafold')).getByText('所有人')).toBeTruthy()
    expect(within(row(screen, 'alphafold')).getByText('teamb')).toBeTruthy()
    expect(within(row(screen, 'cuda')).getByText('仅本团队')).toBeTruthy()
    expect(screen.getAllByText('就绪').length).toBe(2)
    expect(screen.getByText('失败')).toBeTruthy()
    expect(screen.getByText('构建中 (BUILD)')).toBeTruthy()
    expect(screen.getByText('micromamba: package not found')).toBeTruthy()
    expect(screen.getByText(envList.warning!)).toBeTruthy()
    expect(screen.getByText('环境存储')).toBeTruthy()
    expect(screen.getByText('团队 teama 合计')).toBeTruthy()
    expect(screen.getByText('已用 170.0 GB，上限 200 GB')).toBeTruthy()
    // Own versions: delete, change visibility (and retry when failed).
    const cuda = within(row(screen, 'cuda'))
    expect(cuda.getByRole('button', {name: '删除 cuda:12.4'})).toBeTruthy()
    expect(
      cuda.getByRole('button', {name: '更改 cuda 的可见范围'}),
    ).toBeTruthy()
    expect(cuda.queryByText('重试')).toBeNull()
    const af = within(row(screen, 'af'))
    expect(af.getByRole('button', {name: '重试'})).toBeTruthy()
    // Publishing no longer exists.
    expect(screen.queryByText('发布')).toBeNull()
    expect(screen.queryByText(/取消发布/)).toBeNull()
    expect(
      screen.getAllByRole('button', {name: '创建环境'}).length,
    ).toBeGreaterThan(0)
    expect(document.body.textContent).not.toMatch(/billing|points/i)
  })
  it('explains why an action is not available', async () => {
    setState(['identity'], memberIdentity)
    const screen = await renderMember()
    const torch = within(row(screen, 'torch'))
    expect(torch.queryByRole('button', {name: /删除 torch/})).toBeNull()
    fireEvent.click(torch.getByText('删除'))
    await waitFor(() =>
      expect(
        screen.getByText('只有这个版本的创建者、团队负责人或管理员可以删除'),
      ).toBeTruthy(),
    )
    const alphafold = within(row(screen, 'alphafold'))
    fireEvent.click(alphafold.getByText('更改可见范围'))
    await waitFor(() =>
      expect(
        screen.getAllByText('这是其他团队公开的环境，只能使用').length,
      ).toBeGreaterThan(0),
    )
  })
  it('filters by team and searches', async () => {
    setState(['identity'], memberIdentity)
    const screen = await renderMember()
    const select = wrapper(screen.container).findSelect()!
    select.openDropdown()
    expect(screen.getByText('teama（本团队）')).toBeTruthy()
    select.selectOptionByValue('teamb')
    await waitFor(() => expect(screen.queryByText('torch')).toBeNull())
    expect(screen.getByText('alphafold')).toBeTruthy()
    select.openDropdown()
    select.selectOptionByValue('')
    await waitFor(() => expect(screen.getByText('torch')).toBeTruthy())
    wrapper(screen.container).findTextFilter()!.findInput().setInputValue('tor')
    await waitFor(() => expect(screen.queryByText('alphafold')).toBeNull())
    expect(screen.getByText('torch')).toBeTruthy()
    expect(screen.getByText('1 个结果')).toBeTruthy()
  })
  it('creates an environment, 仅本团队 by default', async () => {
    setState(['identity'], memberIdentity)
    ;(CreateEnv as jest.Mock).mockResolvedValue({
      env: {id: 'team/teama/torch/2.5', status: 'queued'},
      usage: envList.usage,
      warning: null,
    })
    const screen = await renderMember()
    fireEvent.click(screen.getAllByRole('button', {name: '创建环境'})[0])
    fireEvent.change(screen.getByLabelText('名称'), {target: {value: 'torch'}})
    fireEvent.change(screen.getByLabelText('版本'), {
      target: {value: '2.5'},
    })
    fireEvent.change(screen.getByLabelText('镜像'), {
      target: {
        value: 'docker.io/pytorch/pytorch:2.5.0-cuda12.4-cudnn9-runtime',
      },
    })
    expect(screen.queryByText('环境内容将对所有用户可读。')).toBeNull()
    fireEvent.click(screen.getByRole('button', {name: '创建'}))
    await waitFor(() => expect(CreateEnv).toHaveBeenCalled())
    expect((CreateEnv as jest.Mock).mock.calls[0]).toEqual([
      memberTarget,
      {
        team: 'teama',
        name: 'torch',
        version: '2.5',
        source: 'image',
        image: 'docker.io/pytorch/pytorch:2.5.0-cuda12.4-cudnn9-runtime',
        visibility: 'team',
      },
    ])
  })
  it('creates an environment for everyone after the readability warning', async () => {
    setState(['identity'], memberIdentity)
    ;(CreateEnv as jest.Mock).mockResolvedValue({
      env: {id: 'team/teama/torch/2.5', status: 'queued'},
      usage: envList.usage,
      warning: null,
    })
    const screen = await renderMember()
    fireEvent.click(screen.getAllByRole('button', {name: '创建环境'})[0])
    fireEvent.change(screen.getByLabelText('名称'), {target: {value: 'torch'}})
    fireEvent.change(screen.getByLabelText('版本'), {
      target: {value: '2.5'},
    })
    fireEvent.change(screen.getByLabelText('镜像'), {
      target: {value: 'docker.io/library/ubuntu:22.04'},
    })
    // the create dialog is the only open one
    fireEvent.click(screen.getAllByLabelText('所有人')[0])
    expect(screen.getByText('环境内容将对所有用户可读。')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', {name: '创建'}))
    await waitFor(() => expect(CreateEnv).toHaveBeenCalled())
    expect((CreateEnv as jest.Mock).mock.calls[0][1].visibility).toBe('all')
  })
  it('changes the visibility of a name after the warning', async () => {
    setState(['identity'], memberIdentity)
    ;(SetEnvVisibility as jest.Mock).mockResolvedValue({
      team: 'teama',
      name: 'cuda',
      visibility: 'all',
      old: 'team',
    })
    const screen = await renderMember()
    fireEvent.click(screen.getByRole('button', {name: '更改 cuda 的可见范围'}))
    expect(screen.getByText(/对 cuda（团队 teama）的所有版本生效/)).toBeTruthy()
    expect(screen.queryByText('环境内容将对所有用户可读。')).toBeNull()
    // the closed create dialog stays mounted: its radio group comes first
    fireEvent.click(screen.getAllByLabelText('所有人').pop()!)
    expect(screen.getByText('环境内容将对所有用户可读。')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', {name: '保存'}))
    await waitFor(() =>
      expect(SetEnvVisibility).toHaveBeenCalledWith(
        memberTarget,
        {team: 'teama', name: 'cuda'},
        'all',
      ),
    )
  })
  it('blocks invalid names before calling the API', async () => {
    setState(['identity'], memberIdentity)
    const screen = await renderMember()
    fireEvent.click(screen.getAllByRole('button', {name: '创建环境'})[0])
    fireEvent.change(screen.getByLabelText('名称'), {target: {value: 'Torch'}})
    fireEvent.change(screen.getByLabelText('版本'), {
      target: {value: '2.5'},
    })
    fireEvent.click(screen.getByRole('button', {name: '创建'}))
    expect(
      screen.getByText(
        "请使用小写字母、数字或 '-'，以字母开头（最多 32 个字符）。",
      ),
    ).toBeTruthy()
    expect(
      screen.getByText('请输入不含空格的镜像地址（最多 256 个字符）。'),
    ).toBeTruthy()
    expect(CreateEnv).not.toHaveBeenCalled()
  })
  it('deletes a version after confirmation', async () => {
    setState(['identity'], memberIdentity)
    ;(DeleteEnv as jest.Mock).mockResolvedValue({result: 'deleted'})
    const screen = await renderMember()
    fireEvent.click(screen.getByRole('button', {name: '删除 cuda:12.4'}))
    expect(
      screen.getByText(/确定删除团队 teama 的 cuda:12.4 吗？/),
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', {name: '确认'}))
    await waitFor(() => expect(DeleteEnv).toHaveBeenCalled())
    const [target, env] = (DeleteEnv as jest.Mock).mock.calls[0]
    expect(target).toEqual(memberTarget)
    expect(env.id).toBe('team/teama/cuda/12.4')
  })
  it('lets a lead who is not a member delete and change visibility, but not create or read logs', async () => {
    setState(['identity'], leadIdentity)
    ;(ListTeamClusters as jest.Mock).mockResolvedValue({
      clusters: [{name: 'hpc-poc', region: 'us-west-2'}],
    })
    ;(ListTeams as jest.Mock).mockResolvedValue(leadTeams)
    ;(ListEnvs as jest.Mock).mockResolvedValue({
      ...envList,
      envs: [
        envEntry({
          can: {delete: true, retry: false},
          can_change_visibility: true,
        }),
      ],
      teams: ['teama'],
      warning: null,
      roles: {member: false, lead: true, admin: false},
    })
    const screen = render(
      <TestProviders>
        <LeadEnvsPage />
      </TestProviders>,
    )
    await waitFor(() =>
      expect(ListEnvs).toHaveBeenCalledWith({
        clusterName: 'hpc-poc',
        region: 'us-west-2',
        team: 'teama',
      }),
    )
    await waitFor(() =>
      expect(screen.getByRole('button', {name: '删除 cuda:12.4'})).toBeTruthy(),
    )
    expect(
      screen.getByRole('button', {name: '更改 cuda 的可见范围'}),
    ).toBeTruthy()
    expect(screen.queryByText('发布')).toBeNull()
    expect(screen.queryByRole('button', {name: '创建环境'})).toBeNull()
    expect(screen.queryByRole('button', {name: '构建日志'})).toBeNull()
  })
})
describe('buildEnvRequest', () => {
  const limits = envList.limits
  it('builds an image request', () => {
    expect(buildEnvRequest(values, limits, t).body).toEqual({
      name: 'cuda',
      version: '12.4',
      source: 'image',
      image: values.image,
    })
  })
  it('sends requirements.txt with the Python version', () => {
    const {body} = buildEnvRequest(
      {
        ...values,
        source: 'conda',
        condaMode: 'requirements_txt',
        condaText: 'numpy\n',
        python: '3.11',
      },
      limits,
      t,
    )
    expect(body).toEqual({
      name: 'cuda',
      version: '12.4',
      source: 'conda',
      requirements_txt: 'numpy\n',
      python: '3.11',
    })
  })
  it('rejects a Dockerfile above the CodeBuild limit and a bad Python version', () => {
    const big = buildEnvRequest(
      {...values, source: 'dockerfile', dockerfile: 'x'.repeat(15001)},
      limits,
      t,
    )
    expect(big.body).toBeNull()
    expect(big.errors.dockerfile).toBe('teamQuota.envs.errors.tooLarge')
    const python = buildEnvRequest(
      {
        ...values,
        source: 'conda',
        condaMode: 'requirements_txt',
        condaText: 'numpy',
        python: '311',
      },
      limits,
      t,
    )
    expect(python.errors.python).toBe('teamQuota.envs.errors.python')
  })
})
