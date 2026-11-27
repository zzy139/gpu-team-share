import {computePeriod} from '../period'
import {
  BUDGET_USD,
  FAIRSHARE,
  parseAmount,
  parseInteger,
  TYPE_LIMIT,
  userFromEmail,
  validateEmail,
  validateInteger,
  validateLifecycleTeamName,
  validateOptionalAmount,
  validateTeamName,
  validateUserName,
} from '../validation'

describe('team-quota validation', () => {
  it.each(['teama', 'a', 'team_x-1', 'a'.repeat(32)])(
    'accepts team name %s',
    name => {
      expect(validateTeamName(name)).toBeNull()
    },
  )

  it.each([
    'teama;rm -rf /',
    '$(id)',
    'TeamA',
    'a'.repeat(33),
    '',
    '1team',
    'team a',
  ])('rejects team name %s', name => {
    expect(validateTeamName(name)).toBe('teamQuota.errors.invalidName')
  })

  it.each(['root', 'pcdefault', 'normal', 'borrow', 'envbuild', 'shared'])(
    'rejects reserved team name %s',
    name => {
      expect(validateTeamName(name)).toBe('teamQuota.errors.reservedName')
    },
  )

  it('validates user names', () => {
    expect(validateUserName('alice')).toBeNull()
    expect(validateUserName('root')).toBe('teamQuota.errors.reservedName')
    expect(validateUserName('bob;id')).toBe('teamQuota.errors.invalidName')
  })

  it('parses only whole numbers', () => {
    expect(parseInteger('6')).toBe(6)
    expect(parseInteger(' -1 ')).toBe(-1)
    expect(parseInteger('6.5')).toBeNull()
    expect(parseInteger('6;reboot')).toBeNull()
    expect(parseInteger('')).toBeNull()
  })

  it('checks the same bounds as the backend', () => {
    expect(validateInteger('0', FAIRSHARE)).toBe('teamQuota.errors.outOfRange')
    expect(validateInteger('10000', FAIRSHARE)).toBeNull()
    expect(validateInteger('1000', TYPE_LIMIT)).toBeNull()
    expect(validateInteger('1001', TYPE_LIMIT)).toBe(
      'teamQuota.errors.outOfRange',
    )
    expect(validateInteger('-1', TYPE_LIMIT)).toBe(
      'teamQuota.errors.outOfRange',
    )
    expect(validateInteger('abc', TYPE_LIMIT)).toBe(
      'teamQuota.errors.invalidNumber',
    )
  })
  it('parses optional budgets in USD', () => {
    expect(parseAmount('99.50')).toBe(99.5)
    expect(parseAmount('1e3')).toBeNull()
    expect(validateOptionalAmount('', BUDGET_USD)).toBeNull()
    expect(validateOptionalAmount('-5', BUDGET_USD)).toBe(
      'teamQuota.errors.invalidAmount',
    )
    expect(validateOptionalAmount('10000001', BUDGET_USD)).toBe(
      'teamQuota.errors.outOfRange',
    )
  })
})

describe('team-quota periods', () => {
  const today = new Date(2026, 10, 2) // 2026-11-02

  it('computes [start, end) periods', () => {
    expect(computePeriod('thisMonth', today)).toEqual({
      start: '2026-11-01',
      end: '2026-11-03',
    })
    expect(computePeriod('lastMonth', today)).toEqual({
      start: '2026-10-01',
      end: '2026-11-01',
    })
    expect(computePeriod('last30Days', today)).toEqual({
      start: '2026-10-04',
      end: '2026-11-03',
    })
  })

  it('includes the end date of a custom range', () => {
    expect(
      computePeriod('custom', today, {
        startDate: '2026/10/30',
        endDate: '2026/10/31',
      }),
    ).toEqual({start: '2026-10-30', end: '2026-11-01'})
    expect(
      computePeriod('custom', today, {startDate: '2026-10-30', endDate: ''}),
    ).toBeNull()
    expect(
      computePeriod('custom', today, {
        startDate: '2026-10-30',
        endDate: '2026-10-01',
      }),
    ).toBeNull()
  })
})

describe('team-quota lifecycle and email validation', () => {
  it('rejects lifecycle team names with "_" or uppercase', () => {
    expect(validateLifecycleTeamName('team-c')).toBeNull()
    expect(validateLifecycleTeamName('team_c')).toBe(
      'teamQuota.errors.invalidLifecycleName',
    )
    expect(validateLifecycleTeamName('TeamC')).toBe(
      'teamQuota.errors.invalidName',
    )
    expect(validateLifecycleTeamName('borrow')).toBe(
      'teamQuota.errors.reservedName',
    )
  })

  it.each([
    ['carol@example.com', 'carol'],
    ['Carol@Example.com', 'carol'],
    ['a.b-c_d@example.com', 'a.b-c_d'],
    ['1abc@example.com', null],
    ['a'.repeat(21) + '@example.com', null],
    ['carol+x@example.com', null],
    ['root@example.com', null],
    ['not-an-email', null],
  ])('derives the cluster user of %s', (email, user) => {
    expect(userFromEmail(email)).toBe(user)
  })

  it('checks the email domain allowlist', () => {
    expect(validateEmail('carol@example.com', ['example.com'])).toBeNull()
    expect(validateEmail('carol@other.com', ['example.com'])).toBe(
      'teamQuota.errors.emailDomain',
    )
    expect(validateEmail('1abc@example.com')).toBe(
      'teamQuota.errors.invalidEmailUser',
    )
    expect(validateEmail('x@y')).toBe('teamQuota.errors.invalidEmail')
  })
})
