import {act, fireEvent, render, waitFor} from '@testing-library/react'
import {clearAllState, setState} from '../../../store'
import {TestProviders} from '../../test-utils/fixtures'
import {
  CancelMemberJob,
  GetMemberMe,
  ListMemberHistory,
  ListMemberJobs,
} from '../api'
import MyJobsPage, {historyPeriod} from '../MyJobsPage'
import {JOBS_REFRESH_MS} from '../queries'
import {me, memberIdentity, memberTarget} from '../test-utils/fixtures'

jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  GetMemberBudget: jest.fn().mockResolvedValue({team: 'teama', budget: null}),
  ListNotifications: jest.fn().mockResolvedValue({items: [], unread: 0}),
  GetMemberMe: jest.fn(),
  ListMemberJobs: jest.fn(),
  ListMemberHistory: jest.fn(),
  CancelMemberJob: jest.fn(),
}))

const pending = {
  job_id: '12',
  name: 'train',
  account: 'teama',
  partition: 'g6e-2xlarge',
  instance_type: 'g6e.2xlarge',
  state: 'PENDING',
  reason: 'QOSGrpNodeLimit',
  reason_text:
    'g6e.2xlarge quota full (2/2). Try g6e.xlarge (1 remaining) or tick borrow',
  submit: null,
  start: null,
  time_limit: '1:00:00',
  elapsed: '0:00',
  gres: 'gres/gpu:1',
  cpus: '4',
  memory: '16G',
  qos: 'teama-g6e-2xlarge',
  alerts: ['no_capacity'],
}
const stuck = {
  ...pending,
  job_id: '14',
  name: 'stuck',
  state: 'RUNNING',
  reason: 'None',
  reason_text: '',
  time_limit: 'UNLIMITED',
  elapsed: '1-02:00:00',
  alerts: ['idle', 'long_run'],
}

describe('MyJobsPage', () => {
  beforeEach(() => {
    clearAllState()
    setState(['identity'], memberIdentity)
    ;(GetMemberMe as jest.Mock).mockResolvedValue(me)
    ;(ListMemberJobs as jest.Mock)
      .mockReset()
      .mockResolvedValue({jobs: [pending]})
    ;(ListMemberHistory as jest.Mock).mockResolvedValue({
      jobs: [
        {
          job_id: '10',
          name: 'old',
          account: 'teama',
          partition: 'g6e-xlarge',
          instance_type: 'g6e.xlarge',
          qos: null,
          state: 'COMPLETED',
          reason: null,
          reason_text: '',
          submit: null,
          start: 's',
          end: 'e',
          elapsed_seconds: 1,
          exit_code: '0:0',
          alloc_tres: null,
          workdir: '/efs/teama',
        },
      ],
    })
    ;(CancelMemberJob as jest.Mock)
      .mockReset()
      .mockResolvedValue({job_id: '12', result: 'cancelled'})
  })

  it('shows own jobs with the pending reason in plain language and the history', async () => {
    const screen = render(
      <TestProviders>
        <MyJobsPage />
      </TestProviders>,
    )
    await waitFor(() =>
      expect(
        screen.getByText(
          'g6e.2xlarge quota full (2/2). Try g6e.xlarge (1 remaining) or tick borrow',
        ),
      ).toBeTruthy(),
    )
    expect(screen.getByText('排队中')).toBeTruthy()
    await waitFor(() => expect(screen.getByText('已完成')).toBeTruthy())
    expect(screen.getByText('g6e.2xlarge')).toBeTruthy()
    expect(screen.getByRole('button', {name: '重新提交作业 10'})).toBeTruthy()
    expect(ListMemberJobs).toHaveBeenCalledWith(memberTarget)
    const period = historyPeriod('7')
    expect(ListMemberHistory).toHaveBeenCalledWith(
      memberTarget,
      period.start,
      period.end,
    )
  })

  it('cancels a job after confirmation', async () => {
    const screen = render(
      <TestProviders>
        <MyJobsPage />
      </TestProviders>,
    )
    await waitFor(
      () =>
        expect(screen.getByRole('button', {name: '取消作业 12'})).toBeTruthy(),
      {timeout: 5000},
    )
    fireEvent.click(screen.getByRole('button', {name: '取消作业 12'}))
    expect(CancelMemberJob).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', {name: '确定取消'}))
    await waitFor(() =>
      expect(CancelMemberJob).toHaveBeenCalledWith(memberTarget, '12'),
    )
  }, 20000)

  it('shows the alert badges, no limit for jobs without a time limit, and an inline cancel', async () => {
    ;(ListMemberJobs as jest.Mock).mockResolvedValue({jobs: [pending, stuck]})
    const screen = render(
      <TestProviders>
        <MyJobsPage />
      </TestProviders>,
    )
    await waitFor(() => expect(screen.getByText('疑似空闲')).toBeTruthy())
    expect(screen.getByText('已运行超24小时')).toBeTruthy()
    expect(screen.getByText('暂无容量')).toBeTruthy()
    expect(screen.getByText('不限')).toBeTruthy()
    expect(screen.getByRole('button', {name: '取消作业 14'})).toBeTruthy()
    expect(screen.getByText(/^最后更新：/)).toBeTruthy()
  })

  it('refreshes every 15 s while the page is visible', async () => {
    jest.useFakeTimers()
    let hidden = false
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => hidden,
    })
    try {
      render(
        <TestProviders>
          <MyJobsPage />
        </TestProviders>,
      )
      await waitFor(() => expect(ListMemberJobs).toHaveBeenCalledTimes(1))
      act(() => {
        jest.advanceTimersByTime(JOBS_REFRESH_MS)
      })
      await waitFor(() => expect(ListMemberJobs).toHaveBeenCalledTimes(2))
      hidden = true
      act(() => {
        document.dispatchEvent(new Event('visibilitychange'))
      })
      act(() => {
        jest.advanceTimersByTime(3 * JOBS_REFRESH_MS)
      })
      expect(ListMemberJobs).toHaveBeenCalledTimes(2)
    } finally {
      hidden = false
      jest.useRealTimers()
    }
  })

  it('computes history ranges of at most 31 days', () => {
    expect(historyPeriod('31', new Date(2026, 10, 30))).toEqual({
      start: '2026-10-31',
      end: '2026-12-01',
    })
    expect(historyPeriod('1', new Date(2026, 10, 30))).toEqual({
      start: '2026-11-30',
      end: '2026-12-01',
    })
  })
})
