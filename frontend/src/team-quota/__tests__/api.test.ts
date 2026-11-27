import {executeRequest} from '../../http/executeRequest'
import {handleNotAuthorizedErrors} from '../../auth/handleNotAuthorizedErrors'
import {getState} from '../../store'
import {
  AddTeamMember,
  AdvanceLifecycleTeam,
  CreateLifecycleTeam,
  CreateTeam,
  DeleteTeam,
  GetCatalog,
  GetLeadQuota,
  GetTeamTrend,
  GetLifecycleTeam,
  GetTeamUsage,
  ListAudit,
  ListLeadJobs,
  ListLifecycleTeams,
  ListTeamClusters,
  ListTeams,
  OffboardTeamMember,
  ReenableTeamMember,
  RemoveTeamMember,
  UpdateTeam,
} from '../api'

jest.mock('../../http/executeRequest', () => ({
  executeRequest: jest.fn(),
}))
jest.mock('../../auth/handleNotAuthorizedErrors', () => ({
  handleNotAuthorizedErrors: jest.fn(),
}))
jest.mock('../../store', () => ({
  getState: jest.fn(),
}))

const mockExecute = executeRequest as jest.Mock
const target = {clusterName: 'hpc-poc', region: 'us-west-2'}
const Q = 'cluster_name=hpc-poc&region=us-west-2'

describe('team-quota api', () => {
  beforeEach(() => {
    mockExecute.mockReset()
    mockExecute.mockResolvedValue({data: {ok: true}})
    ;(getState as jest.Mock).mockReset()
  })

  it.each([
    [
      () => ListTeamClusters('us-west-2'),
      'get',
      'team-quota/clusters?region=us-west-2',
      undefined,
    ],
    [() => ListTeams(target), 'get', `team-quota/teams?${Q}`, undefined],
    [
      () => ListTeams(target, 'teama'),
      'get',
      `team-quota/teams?${Q}&team=teama`,
      undefined,
    ],
    [
      () =>
        CreateTeam(target, {
          name: 'qatest',
          fairshare: 10,
          type_limits: {'g6e.xlarge': 2},
        }),
      'post',
      `team-quota/teams?${Q}`,
      {name: 'qatest', fairshare: 10, type_limits: {'g6e.xlarge': 2}},
    ],
    [
      () => UpdateTeam(target, 'teamb', {type_limits: {'g6e.xlarge': 0}}),
      'patch',
      `team-quota/teams/teamb?${Q}`,
      {type_limits: {'g6e.xlarge': 0}},
    ],
    [() => GetCatalog(target), 'get', `team-quota/catalog?${Q}`, undefined],
    [
      () => OffboardTeamMember(target, 'teamb', 'bob', 'finish'),
      'post',
      `team-quota/teams/teamb/members/bob/offboard?${Q}`,
      {jobs: 'finish'},
    ],
    [
      () => ReenableTeamMember(target, 'teamb', 'bob'),
      'post',
      `team-quota/teams/teamb/members/bob/reenable?${Q}`,
      {},
    ],
    [
      () => ListAudit({month: '2026-11', actor: 'admin'}),
      'get',
      'team-quota/audit?month=2026-11&actor=admin',
      undefined,
    ],
    [
      () => ListLeadJobs(target, 'teama'),
      'get',
      `team-quota/lead/jobs?${Q}&team=teama`,
      undefined,
    ],
    [
      () => GetLeadQuota(target, 'teama'),
      'get',
      `team-quota/lead/quota?${Q}&team=teama`,
      undefined,
    ],
    [
      () => DeleteTeam(target, 'qatest'),
      'delete',
      `team-quota/teams/qatest?${Q}`,
      undefined,
    ],
    [
      () => AddTeamMember(target, 'teamb', {user: 'carol'}),
      'post',
      `team-quota/teams/teamb/members?${Q}`,
      {user: 'carol'},
    ],
    [
      () =>
        AddTeamMember(target, 'teamb', {
          email: 'carol@example.com',
          link_existing: true,
        }),
      'post',
      `team-quota/teams/teamb/members?${Q}`,
      {email: 'carol@example.com', link_existing: true},
    ],
    [
      () => ListLifecycleTeams(target),
      'get',
      `team-quota/lifecycle/teams?${Q}`,
      undefined,
    ],
    [
      () => GetLifecycleTeam(target, 'teamc'),
      'get',
      `team-quota/lifecycle/teams/teamc?${Q}`,
      undefined,
    ],
    [
      () =>
        CreateLifecycleTeam(target, {
          name: 'teamc',
          fairshare: 10,
          type_limits: {'g6e.xlarge': 1},
          members: [{email: 'carol@example.com'}],
          lead: 'lead@example.com',
        }),
      'post',
      `team-quota/lifecycle/teams?${Q}`,
      {
        name: 'teamc',
        fairshare: 10,
        type_limits: {'g6e.xlarge': 1},
        members: [{email: 'carol@example.com'}],
        lead: 'lead@example.com',
      },
    ],
    [
      () => AdvanceLifecycleTeam(target, 'teamc'),
      'post',
      `team-quota/lifecycle/teams/teamc/advance?${Q}`,
      {},
    ],
    [
      () => RemoveTeamMember(target, 'teamb', 'bob'),
      'delete',
      `team-quota/teams/teamb/members/bob?${Q}`,
      undefined,
    ],
    [
      () => GetTeamUsage(target, '2026-11-01', '2026-12-01'),
      'get',
      `team-quota/usage?${Q}&start=2026-11-01&end=2026-12-01`,
      undefined,
    ],
    [
      () => GetTeamTrend(target, 6),
      'get',
      `team-quota/trend?${Q}&months=6`,
      undefined,
    ],
  ])('calls the expected endpoint (%#)', async (call, method, url, body) => {
    await expect(call()).resolves.toEqual({ok: true})
    expect(mockExecute).toHaveBeenCalledWith(method, url, body, {})
  })

  it('URL-encodes path and query values', async () => {
    await UpdateTeam({clusterName: 'a b'}, 'x/../y', {fairshare: 1})
    expect(mockExecute.mock.calls[0][1]).toBe(
      'team-quota/teams/x%2F..%2Fy?cluster_name=a%20b',
    )
  })

  it('uses the selected region when the target has none', async () => {
    ;(getState as jest.Mock).mockImplementation(path =>
      path.join('.') === 'app.selectedRegion' ? 'us-west-2' : undefined,
    )
    await ListTeams({clusterName: 'hpc-poc'})
    expect(mockExecute.mock.calls[0][1]).toBe(`team-quota/teams?${Q}`)
  })

  it('redirects to login only on 401', async () => {
    const appConfig = {authUrl: 'x'}
    ;(getState as jest.Mock).mockImplementation(path =>
      path.join('.') === 'app.appConfig' ? appConfig : undefined,
    )
    const redirect = jest.fn().mockImplementation(p => p)
    ;(handleNotAuthorizedErrors as jest.Mock).mockReturnValue(redirect)

    mockExecute.mockRejectedValueOnce({response: {status: 403}})
    await expect(ListTeams(target)).rejects.toEqual({response: {status: 403}})
    expect(redirect).not.toHaveBeenCalled()

    mockExecute.mockRejectedValueOnce({response: {status: 401}})
    await expect(ListTeams(target)).rejects.toEqual({response: {status: 401}})
    expect(handleNotAuthorizedErrors).toHaveBeenCalledWith(appConfig)
    expect(redirect).toHaveBeenCalledTimes(1)
  })
})
