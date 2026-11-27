import {fireEvent, render, waitFor} from '@testing-library/react'
import {clearAllState, setState} from '../../store'
import {ListAudit} from '../api'
import AuditPage, {auditFilters} from '../AuditPage'
import {adminIdentity, TestProviders} from '../test-utils/fixtures'
jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  ListAudit: jest.fn(),
}))
const response = {
  months: ['2026-11'],
  truncated: false,
  entries: [
    {
      time: '2026-11-04T02:00:00Z',
      actor: 'uuid-admin',
      actor_email: 'admin@example.com',
      action: 'env_publish',
      cluster: 'hpc-poc',
      team: 'teama',
      params: '{"name": "cuda", "version": "12.4"}',
      result: 'success',
      error: null,
    },
    {
      time: '2026-11-04T01:00:00Z',
      actor: 'uuid-carol',
      actor_email: 'carol@example.com',
      action: 'env_create',
      cluster: 'hpc-poc',
      team: 'teama',
      params: '{"name": "x"}',
      result: 'rejected',
      error: 'version exists',
    },
  ],
}
function input(screen: any, label: string) {
  return screen
    .getAllByLabelText(label)
    .find((el: HTMLElement) => el.tagName === 'INPUT') as HTMLElement
}
const empty = {month: '', from: '', to: '', actor: '', team: '', action: ''}
describe('AuditPage', () => {
  beforeEach(() => {
    clearAllState()
    jest.clearAllMocks()
    setState(['identity'], adminIdentity)
    ;(ListAudit as jest.Mock).mockResolvedValue(response)
  })
  it('lists the entries and sends the filters', async () => {
    const screen = render(
      <TestProviders>
        <AuditPage />
      </TestProviders>,
    )
    await waitFor(() => expect(screen.getByText('env_publish')).toBeTruthy())
    expect(ListAudit).toHaveBeenCalledWith({})
    expect(screen.getAllByText('carol@example.com').length).toBe(1)
    expect(screen.getByText('version exists')).toBeTruthy()
    fireEvent.change(input(screen, '团队'), {target: {value: 'teama'}})
    fireEvent.change(input(screen, '操作'), {
      target: {value: 'env_create'},
    })
    fireEvent.change(input(screen, '月份'), {
      target: {value: '2026-11'},
    })
    fireEvent.click(screen.getByRole('button', {name: '应用筛选'}))
    await waitFor(() =>
      expect(ListAudit).toHaveBeenLastCalledWith({
        month: '2026-11',
        team: 'teama',
        action: 'env_create',
      }),
    )
  })
  it('shows field errors and keeps the previous filters', async () => {
    const screen = render(
      <TestProviders>
        <AuditPage />
      </TestProviders>,
    )
    await waitFor(() => expect(ListAudit).toHaveBeenCalledTimes(1))
    fireEvent.change(input(screen, '月份'), {
      target: {value: '2026-13'},
    })
    fireEvent.click(screen.getByRole('button', {name: '应用筛选'}))
    expect(screen.getByText('格式为 YYYY-MM。')).toBeTruthy()
    expect(ListAudit).toHaveBeenCalledTimes(1)
  })
})
describe('auditFilters', () => {
  it('drops empty values and the date range when a month is set', () => {
    expect(
      auditFilters({
        ...empty,
        month: '2026-11',
        from: '2026-10-01',
        actor: ' a ',
      }).filters,
    ).toEqual({month: '2026-11', actor: 'a'})
    expect(
      auditFilters({...empty, from: '2026-10-01', to: '2026-11-01'}).filters,
    ).toEqual({from: '2026-10-01', to: '2026-11-01'})
  })
  it('rejects bad team and action values', () => {
    const {filters, errors} = auditFilters({
      ...empty,
      team: 'Team A',
      action: 'env-create',
    })
    expect(filters).toBeNull()
    expect(errors).toMatchObject({team: true, action: true})
  })
})
