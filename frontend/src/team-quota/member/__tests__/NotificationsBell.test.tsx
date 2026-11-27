import {fireEvent, render, waitFor} from '@testing-library/react'
import {clearAllState, setState} from '../../../store'
import {TestProviders} from '../../test-utils/fixtures'
import {
  GetPreferences,
  ListNotifications,
  MarkNotificationsRead,
  UpdatePreferences,
} from '../api'
import NotificationsBell from '../NotificationsBell'
import {memberIdentity, memberTarget} from '../test-utils/fixtures'
jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  ListNotifications: jest.fn(),
  MarkNotificationsRead: jest.fn(),
  GetPreferences: jest.fn(),
  UpdatePreferences: jest.fn(),
}))
const notifications = {
  unread: 1,
  items: [
    {
      id: 'n1',
      kind: 'job',
      job_id: '42',
      name: 'fold',
      state: 'COMPLETED',
      exit_code: '0:0',
      partition: 'g6e-xlarge',
      instance_type: 'g6e.xlarge',
      env: null,
      end: '2026-11-04T02:00:00Z',
      team: 'teama',
      unread: true,
    },
    {
      id: 'n2',
      kind: 'budget',
      job_id: null,
      name: null,
      state: null,
      exit_code: null,
      partition: null,
      instance_type: null,
      env: null,
      end: null,
      team: 'teama',
      unread: false,
    },
    {
      id: 'n3',
      kind: 'gpu_idle',
      job_id: '43',
      name: 'stuck',
      state: null,
      exit_code: null,
      partition: 'g6e-xlarge',
      instance_type: 'g6e.xlarge',
      env: null,
      end: null,
      team: 'teama',
      hours: 2,
      minutes: 60,
      text: '作业 43（stuck，g6e.xlarge）已运行 2 小时，最近 60 分钟 GPU 利用率一直低于 5%，可能已卡住或在空等。',
      unread: false,
    },
    {
      id: 'n4',
      kind: 'long_run',
      job_id: '44',
      name: 'long',
      state: null,
      exit_code: null,
      partition: 'g6e-xlarge',
      instance_type: 'g6e.xlarge',
      env: null,
      end: null,
      team: 'teama',
      hours: 24,
      minutes: null,
      text: null,
      unread: false,
    },
    {
      id: 'n5',
      kind: 'no_capacity',
      job_id: '45',
      name: 'wait',
      state: null,
      exit_code: null,
      partition: 'p5-48xlarge',
      instance_type: 'p5.48xlarge',
      env: null,
      end: null,
      team: 'teama',
      hours: 2,
      minutes: null,
      text: null,
      unread: false,
    },
  ],
}
function renderBell() {
  return render(
    <TestProviders>
      <NotificationsBell target={memberTarget} />
    </TestProviders>,
  )
}
describe('NotificationsBell', () => {
  beforeEach(() => {
    clearAllState()
    jest.clearAllMocks()
    setState(['identity'], memberIdentity)
    ;(ListNotifications as jest.Mock).mockResolvedValue(notifications)
    ;(MarkNotificationsRead as jest.Mock).mockResolvedValue({unread: 0})
    ;(GetPreferences as jest.Mock).mockResolvedValue({
      job_notify: true,
      email_jobs: false,
      email_budget: false,
      email_alerts: true,
      email: {address: 'carol@example.com', status: 'none'},
    })
    ;(UpdatePreferences as jest.Mock).mockResolvedValue({
      job_notify: true,
      email_jobs: true,
      email_budget: false,
      email_alerts: true,
      email: {address: 'carol@example.com', status: 'pending'},
    })
  })
  it('shows the unread count, the job-end and budget notifications, and marks them read', async () => {
    const screen = renderBell()
    await waitFor(() =>
      expect(screen.getByRole('button', {name: '通知，1 条未读'})).toBeTruthy(),
    )
    expect(screen.getByText('通知（1）')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', {name: '通知，1 条未读'}))
    expect(screen.getByText(/作业 42（fold，g6e\.xlarge）/)).toBeTruthy()
    expect(screen.getByText('团队 teama 预算提醒')).toBeTruthy()
    // job alerts: the server's Chinese text, or the built-in text
    expect(screen.getByText('GPU 疑似空闲')).toBeTruthy()
    expect(
      screen.getByText(/作业 43（stuck，g6e\.xlarge）已运行 2 小时/),
    ).toBeTruthy()
    expect(screen.getByText('运行时间提醒')).toBeTruthy()
    expect(
      screen.getByText(/作业 44（long，g6e\.xlarge）已运行 24 小时/),
    ).toBeTruthy()
    expect(screen.getByText('暂无容量')).toBeTruthy()
    expect(
      screen.getByText(/作业 45（wait，p5\.48xlarge）已排队 2 小时/),
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', {name: '全部标为已读'}))
    await waitFor(() =>
      expect(MarkNotificationsRead).toHaveBeenCalledWith(memberTarget),
    )
  })
  it('changes an e-mail preference and shows the subscription state', async () => {
    const screen = renderBell()
    fireEvent.click(screen.getByRole('button', {name: '通知设置'}))
    await waitFor(() =>
      expect(
        screen.getByText('邮件通知已关闭（carol@example.com）。'),
      ).toBeTruthy(),
    )
    fireEvent.click(screen.getByLabelText('作业结束时给我发邮件'))
    await waitFor(() =>
      expect(UpdatePreferences).toHaveBeenCalledWith(memberTarget, {
        email_jobs: true,
      }),
    )
  })
  it('turns the e-mail alerts off and explains the confirmation mail', async () => {
    const screen = renderBell()
    fireEvent.click(screen.getByRole('button', {name: '通知设置'}))
    const toggle = await waitFor(
      () =>
        screen.getByLabelText(
          '邮件提醒（GPU 空闲 / 运行时间长 / 暂无容量）',
        ) as HTMLInputElement,
    )
    await waitFor(() => expect(toggle.checked).toBe(true))
    expect(
      screen.getByText(/AWS 会向 carol@example\.com 发送一封确认邮件/),
    ).toBeTruthy()
    fireEvent.click(toggle)
    await waitFor(() =>
      expect(UpdatePreferences).toHaveBeenCalledWith(memberTarget, {
        email_alerts: false,
      }),
    )
  })
})
