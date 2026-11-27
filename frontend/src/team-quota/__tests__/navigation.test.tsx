import {render, waitFor} from '@testing-library/react'
import {I18nextProvider} from 'react-i18next'
import {QueryClient, QueryClientProvider} from 'react-query'
import {Provider} from 'react-redux'
import SideBar from '../../components/SideBar'
import i18n from '../../i18n'
import {LoadInitialState} from '../../model'
import UpstreamApp from '../../pages/index'
import {clearAllState, setState, store} from '../../store'
import {ListTeamClusters, ListTeams} from '../api'
import {GetMemberMe, ListMemberHistory, ListMemberJobs} from '../member/api'
import {
  adminIdentity,
  adminTeams,
  leadIdentity,
  TestProviders,
} from '../test-utils/fixtures'

jest.mock('../../model', () => ({
  ...(jest.requireActual('../../model') as any),
  LoadInitialState: jest.fn(),
}))
jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  ListTeamClusters: jest.fn(),
  ListTeams: jest.fn(),
  ListLifecycleTeams: jest.fn().mockResolvedValue({
    configured: false,
    records: [],
    email_domains: [],
  }),
  GetCapacity: jest.fn().mockResolvedValue({
    types: [],
    teams: [],
    unmanaged_queues: [],
    partitions: [],
  }),
  GetTeamUsage: jest.fn().mockResolvedValue({
    period: {start: '2026-11-01', end: '2026-11-03'},
    project: 'hpc-poc',
    teams: [],
    billed_available: true,
    prices: {},
    sources: {billed: 'cost-explorer', estimate: 'pricing-api'},
    notes: [],
  }),
  GetTeamTrend: jest.fn().mockResolvedValue({
    months: [],
    billed_available: true,
    sources: {billed: 'cost-explorer', estimate: 'pricing-api'},
    notes: [],
  }),
}))

jest.mock('../member/api', () => ({
  ...(jest.requireActual('../member/api') as any),
  GetMemberMe: jest.fn(),
  ListMemberJobs: jest.fn(),
  ListMemberHistory: jest.fn(),
  GetMemberBudget: jest.fn().mockResolvedValue({team: 'teama', budget: null}),
  ListNotifications: jest.fn().mockResolvedValue({items: [], unread: 0}),
}))

const memberIdentity = {
  user_roles: ['member-teama'],
  username: 'uuid-carol',
  attributes: {email: 'carol@example.com'},
}
const leadMemberIdentity = {
  ...memberIdentity,
  user_roles: ['team-teama', 'member-teama'],
}
const adminMemberIdentity = {
  ...memberIdentity,
  user_roles: ['admin', 'member-teama'],
}
// The side navigation header is a link too.
const HEADER = 'AWS ParallelCluster'
const LICENSE = '查看许可证'
const ADMIN_LINKS = [
  '集群',
  '机型与节点',
  '镜像',
  '团队管理',
  '费用总览',
  '管理员账号',
  '审计日志',
]
const LEAD_LINKS = ['我的团队', '费用总览']
const MEMBER_LINKS = ['我的作业', '提交作业', '环境', '我的团队']

// The page component returns ReactNode; wrap it so it can be rendered as JSX.
const App = UpstreamApp as unknown as () => JSX.Element

function sideBarLinks(identity: any) {
  setState(['identity'], identity)
  const screen = render(
    <TestProviders>
      <SideBar />
    </TestProviders>,
  )
  return screen.getAllByRole('link').map(link => link.textContent)
}

function prepareLoadedState(identity: any) {
  setState(['identity'], identity)
  setState(['app', 'appConfig'], {
    authUrl: 'https://auth.example.com',
    clientId: 'client',
    scopes: 'openid',
    redirectUri: 'https://pcui.example.com/login',
  })
  setState(['app', 'version', 'full'], ['3.16.2'])
}

function renderApp(identity: any, path: string) {
  prepareLoadedState(identity)
  window.history.pushState({}, '', path)
  const queryClient = new QueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <Provider store={store}>
          <App />
        </Provider>
      </I18nextProvider>
    </QueryClientProvider>,
  )
}

describe('team-quota navigation', () => {
  beforeEach(() => {
    clearAllState()
    ;(LoadInitialState as jest.Mock).mockClear()
    ;(ListTeamClusters as jest.Mock).mockResolvedValue({clusters: []})
    ;(ListTeams as jest.Mock).mockResolvedValue({
      teams: [],
      catalog: [],
      unmanaged_queues: [],
      editable: false,
    })
  })

  it('gives admins 集群 (with 机型与节点 and 镜像) | 团队管理, 费用总览 | 管理员账号, 审计日志', () => {
    const screen = sideBarLinks(adminIdentity)
    expect(screen).toEqual([HEADER, ...ADMIN_LINKS, LICENSE])
  })

  it('gives team leads 我的团队 and 费用总览 only', () => {
    expect(sideBarLinks(leadIdentity)).toEqual([HEADER, ...LEAD_LINKS, LICENSE])
  })

  it('gives members 我的作业, 提交作业, 环境 and 我的团队', () => {
    expect(sideBarLinks(memberIdentity)).toEqual([
      HEADER,
      ...MEMBER_LINKS,
      LICENSE,
    ])
  })

  it('gives a lead who is also a member the union, every page once', () => {
    expect(sideBarLinks(leadMemberIdentity)).toEqual([
      HEADER,
      '我的团队',
      '费用总览',
      '我的作业',
      '提交作业',
      '环境',
      LICENSE,
    ])
  })

  it('gives an admin who is also a member the admin menu plus the member pages', () => {
    expect(sideBarLinks(adminMemberIdentity)).toEqual([
      HEADER,
      ...ADMIN_LINKS,
      ...MEMBER_LINKS,
      LICENSE,
    ])
  })

  it('opens 我的团队 for team leads without loading admin data', async () => {
    renderApp(leadIdentity, '/pcui/')
    await waitFor(() => expect(window.location.pathname).toBe('/pcui/my-team'))
    expect(LoadInitialState).not.toHaveBeenCalled()
  })

  it('sends a lead from the old team jobs page to the 作业 tab of 我的团队', async () => {
    renderApp(leadIdentity, '/pcui/team-jobs')
    await waitFor(() =>
      expect(window.location.pathname).toBe('/pcui/my-team/jobs'),
    )
  })

  it('sends an admin from the old quota holders page to the 成员 tab of the team', async () => {
    ;(ListTeamClusters as jest.Mock).mockResolvedValue({
      clusters: [{name: 'hpc-poc', region: 'us-west-2'}],
    })
    ;(ListTeams as jest.Mock).mockResolvedValue(adminTeams)
    renderApp(adminIdentity, '/pcui/quota-holders')
    await waitFor(() =>
      expect(window.location.pathname).toBe('/pcui/teams/teama/members'),
    )
    await waitFor(() => expect(LoadInitialState).toHaveBeenCalled())
  })

  it('opens 机型与节点 for admins', async () => {
    const screen = renderApp(adminIdentity, '/pcui/capacity')
    await waitFor(() =>
      expect(screen.getAllByText('各团队配额和用量').length).toBeGreaterThan(0),
    )
  })

  it('sends members from the old templates page to 提交作业', async () => {
    ;(GetMemberMe as jest.Mock).mockResolvedValue({
      email: 'carol@example.com',
      user: 'carol',
      teams: [{team: 'teama', team_path: '/efs/teama'}],
      clusters: [{name: 'hpc-poc', region: 'us-west-2'}],
    })
    renderApp(memberIdentity, '/pcui/templates')
    await waitFor(() => expect(window.location.pathname).toBe('/pcui/submit'))
    expect(window.location.search).toBe('?tab=templates')
  })

  it('renders the member router without loading admin data', async () => {
    ;(ListTeamClusters as jest.Mock).mockClear()
    ;(GetMemberMe as jest.Mock).mockResolvedValue({
      email: 'carol@example.com',
      user: 'carol',
      teams: [{team: 'teama', team_path: '/efs/teama'}],
      clusters: [{name: 'hpc-poc', region: 'us-west-2'}],
    })
    ;(ListMemberJobs as jest.Mock).mockResolvedValue({jobs: []})
    ;(ListMemberHistory as jest.Mock).mockResolvedValue({jobs: []})
    const screen = renderApp(memberIdentity, '/pcui/')
    await waitFor(() =>
      expect(
        screen.getAllByText('运行中和排队中的作业').length,
      ).toBeGreaterThan(0),
    )
    expect(window.location.pathname).toBe('/pcui/my-jobs')
    expect(LoadInitialState).not.toHaveBeenCalled()
    expect(ListTeamClusters).not.toHaveBeenCalled()
  })
})
