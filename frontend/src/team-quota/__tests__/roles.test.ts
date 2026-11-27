import {
  hasMemberRole,
  isAdmin,
  isMemberOnly,
  isTeamLeadOnly,
  memberTeamsFromRoles,
  teamsFromRoles,
} from '../roles'

const identity = (roles: string[]) => ({user_roles: roles as any})

describe('team-quota roles', () => {
  it('recognizes admins', () => {
    expect(isAdmin(identity(['admin']))).toBe(true)
    expect(isAdmin(identity(['team-teama']))).toBe(false)
    expect(isAdmin(null)).toBe(false)
    expect(isAdmin(undefined)).toBe(false)
  })

  it('extracts valid team names from team-* groups', () => {
    expect(
      teamsFromRoles(
        identity(['team-teamb', 'team-teama', 'team-teama', 'admin']),
      ),
    ).toEqual(['teama', 'teamb'])
    expect(
      teamsFromRoles(
        identity(['team-', 'team-TeamA', 'team-root', 'team-a;b', 'teams']),
      ),
    ).toEqual([])
  })

  it('treats team groups without admin as team-lead only', () => {
    expect(isTeamLeadOnly(identity(['team-teama']))).toBe(true)
    expect(isTeamLeadOnly(identity(['team-teama', 'admin']))).toBe(false)
    expect(isTeamLeadOnly(identity(['viewer']))).toBe(false)
    expect(isTeamLeadOnly(null)).toBe(false)
  })

  it('extracts member teams from member-* groups only', () => {
    expect(
      memberTeamsFromRoles(
        identity(['member-teamb', 'member-teama', 'team-teamc']),
      ),
    ).toEqual(['teama', 'teamb'])
    expect(
      memberTeamsFromRoles(identity(['member-', 'member-Root', 'member-root'])),
    ).toEqual([])
    // member groups never grant team-lead scope
    expect(teamsFromRoles(identity(['member-teama']))).toEqual([])
    expect(isTeamLeadOnly(identity(['member-teama']))).toBe(false)
  })

  it('treats member groups without admin or lead groups as member only', () => {
    expect(isMemberOnly(identity(['member-teama']))).toBe(true)
    expect(isMemberOnly(identity(['member-teama', 'team-teama']))).toBe(false)
    expect(isMemberOnly(identity(['member-teama', 'admin']))).toBe(false)
    expect(hasMemberRole(identity(['member-teama', 'admin']))).toBe(true)
    expect(hasMemberRole(identity(['team-teama']))).toBe(false)
  })
})
