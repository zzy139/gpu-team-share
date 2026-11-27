import {act, fireEvent, render, waitFor} from '@testing-library/react'
import {Route, Routes} from 'react-router-dom'
import {clearAllState, setState} from '../../../store'
import {TestProviders} from '../../test-utils/fixtures'
import {GetMemberJob, GetMemberMe, ListMemberDir, ReadMemberFile} from '../api'
import JobDetailPage from '../JobDetailPage'
import {clockTime} from '../JobAlerts'
import {CHUNK_BYTES} from '../LogViewer'
import {JOBS_REFRESH_MS} from '../queries'
import {me, memberIdentity, memberTarget} from '../test-utils/fixtures'

jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  GetMemberBudget: jest.fn().mockResolvedValue({team: 'teama', budget: null}),
  ListNotifications: jest.fn().mockResolvedValue({items: [], unread: 0}),
  GetMemberMe: jest.fn(),
  GetMemberJob: jest.fn(),
  ReadMemberFile: jest.fn(),
  ListMemberDir: jest.fn(),
}))

const b64 = (text: string) => Buffer.from(text, 'utf-8').toString('base64')
const running = {
  job_id: '12',
  name: 'train',
  user: 'carol',
  state: 'RUNNING',
  reason: 'None',
  reason_text: '',
  start: '2026-11-17T01:00:00',
  end: '2026-11-17T05:00:00',
  time_limit: 'UNLIMITED',
  workdir: '/efs/teama',
  stdout_path: '/efs/teama/slurm-12.out',
  stderr_path: '/efs/teama/slurm-12.out',
  team_root: '/efs/teama',
  output_state: 'ok',
  end_is_projected: true,
  alerts: [],
}
const chunk = (text: string, offset = 0) => ({
  path: '/efs/teama/slurm-12.out',
  size: 20000,
  offset,
  length: CHUNK_BYTES,
  content_b64: b64(text),
  truncated: offset > 0,
  limit: CHUNK_BYTES,
})

function renderPage() {
  return render(
    <TestProviders path="/my-jobs/12">
      <Routes>
        <Route path="/my-jobs/:jobId" element={<JobDetailPage />} />
      </Routes>
    </TestProviders>,
  )
}

/** Value of a key-value field of the job details (the file table has columns with the same names). */
function fieldValue(screen: any, label: string) {
  const key = (screen.getAllByText(label) as HTMLElement[]).find(el =>
    el.className.includes('key-label'),
  )!
  return key.nextSibling!.textContent
}

let hidden = false
function setHidden(value: boolean) {
  hidden = value
  act(() => {
    document.dispatchEvent(new Event('visibilitychange'))
  })
}

describe('JobDetailPage', () => {
  beforeAll(() => {
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => hidden,
    })
  })
  beforeEach(() => {
    hidden = false
    clearAllState()
    setState(['identity'], memberIdentity)
    ;(GetMemberMe as jest.Mock).mockResolvedValue(me)
    ;(GetMemberJob as jest.Mock).mockReset().mockResolvedValue(running)
    ;(ListMemberDir as jest.Mock).mockResolvedValue({
      path: '/efs/teama',
      truncated: false,
      entries: [{type: 'file', size: 20000, mtime: 1, name: 'slurm-12.out'}],
    })
    ;(ReadMemberFile as jest.Mock).mockReset()
  })
  afterEach(() => {
    jest.useRealTimers()
  })

  it('shows the job, tails the log and loads earlier content', async () => {
    ;(ReadMemberFile as jest.Mock)
      .mockResolvedValueOnce(chunk('tail part 中文\n', 3616))
      .mockResolvedValueOnce({...chunk('head part\n'), length: 3616})
    const screen = renderPage()
    await waitFor(() =>
      expect(GetMemberJob).toHaveBeenCalledWith(memberTarget, '12'),
    )
    await waitFor(() =>
      expect(screen.getByTestId('log-content').textContent).toBe(
        'tail part 中文\n',
      ),
    )
    expect(ReadMemberFile).toHaveBeenCalledWith(
      memberTarget,
      '/efs/teama/slurm-12.out',
      'tail',
      0,
      CHUNK_BYTES,
    )
    expect(screen.getAllByTestId('log-content')).toHaveLength(1) // stdout == stderr is shown once
    fireEvent.click(screen.getByRole('button', {name: '加载更早内容'}))
    await waitFor(() =>
      expect(screen.getByTestId('log-content').textContent).toBe(
        'head part\ntail part 中文\n',
      ),
    )
    expect(ReadMemberFile).toHaveBeenLastCalledWith(
      memberTarget,
      '/efs/teama/slurm-12.out',
      'offset',
      0,
      3616,
    )
    await waitFor(() =>
      expect(ListMemberDir).toHaveBeenCalledWith(memberTarget, '/efs/teama'),
    )
    // running, no time limit: no end time yet
    expect(fieldValue(screen, '结束时间')).toBe('-')
    expect(fieldValue(screen, '最长运行时间')).toBe('不限')
  })

  it('explains a job that has not started instead of showing a read error', async () => {
    ;(GetMemberJob as jest.Mock).mockResolvedValue({
      ...running,
      state: 'PENDING',
      reason: 'None',
      reason_text: '正在等待机器启动',
      start: null,
      end: '2026-11-17T05:00:00',
      time_limit: null,
      output_state: 'not_started',
    })
    const screen = renderPage()
    await waitFor(() =>
      expect(
        screen.getByText(
          '作业尚未开始运行，节点启动后会生成输出文件（约 5 分钟）。',
        ),
      ).toBeTruthy(),
    )
    expect(screen.getByText('正在等待机器启动')).toBeTruthy()
    expect(fieldValue(screen, '结束时间')).toBe('-')
    expect(screen.queryByText('找不到输出文件')).toBeNull()
    expect(document.querySelector('[data-testid="log-content"]')).toBeNull()
    expect(ReadMemberFile).not.toHaveBeenCalled()
  })

  it('shows the reasons when the output file of a job that ran is missing', async () => {
    ;(GetMemberJob as jest.Mock).mockResolvedValue({
      ...running,
      state: 'FAILED',
      end: '2026-11-17T02:00:00',
      end_is_projected: false,
      output_state: 'missing',
    })
    const screen = renderPage()
    await waitFor(() => expect(screen.getByText('找不到输出文件')).toBeTruthy())
    expect(screen.getByText('工作目录对您的账号不可写；')).toBeTruthy()
    expect(
      screen.getByText('输出路径（#SBATCH --output）所在的目录不存在；'),
    ).toBeTruthy()
    expect(fieldValue(screen, '结束时间')).toBe('2026-11-17T02:00:00')
    expect(ReadMemberFile).not.toHaveBeenCalled()
  })

  it('says who deleted the environment of a build job', async () => {
    ;(GetMemberJob as jest.Mock).mockResolvedValue({
      ...running,
      name: 'env-cuda',
      state: 'COMPLETED',
      end_is_projected: false,
      output_state: 'env_deleted',
      env_deleted: {
        by: 'carol',
        at: '2026-11-16T08:00:00Z',
        team: 'teama',
        name: 'cuda',
        version: '12.4',
      },
    })
    const screen = renderPage()
    await waitFor(() =>
      expect(
        screen.getByText(
          '环境 cuda:12.4 已被删除（由 carol 于 2026-11-16T08:00:00Z），构建日志也随之删除。',
        ),
      ).toBeTruthy(),
    )
    expect(ReadMemberFile).not.toHaveBeenCalled()
  })

  it('refreshes every 15 s with the last update time and follows the log while running, and stops when the job ends', async () => {
    jest.useFakeTimers()
    ;(ReadMemberFile as jest.Mock).mockResolvedValue(chunk('line 1\n'))
    const screen = renderPage()
    await waitFor(() => expect(GetMemberJob).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByText(/^最后更新：/)).toBeTruthy())
    expect(screen.getByText('作业结束前每 15 秒自动刷新。')).toBeTruthy()
    await waitFor(() => expect(ReadMemberFile).toHaveBeenCalledTimes(1))
    ;(ReadMemberFile as jest.Mock).mockResolvedValue(chunk('line 1\nline 2\n'))
    act(() => {
      jest.advanceTimersByTime(JOBS_REFRESH_MS)
    })
    await waitFor(() => expect(GetMemberJob).toHaveBeenCalledTimes(2))
    // the tail of a running job is read again
    await waitFor(() =>
      expect(screen.getByTestId('log-content').textContent).toBe(
        'line 1\nline 2\n',
      ),
    )
    ;(GetMemberJob as jest.Mock).mockResolvedValue({
      ...running,
      state: 'COMPLETED',
      end: '2026-11-17T03:00:00',
      end_is_projected: false,
    })
    ;(ReadMemberFile as jest.Mock).mockResolvedValue(
      chunk('line 1\nline 2\ndone\n'),
    )
    act(() => {
      jest.advanceTimersByTime(JOBS_REFRESH_MS)
    })
    await waitFor(() => expect(GetMemberJob).toHaveBeenCalledTimes(3))
    await waitFor(() => expect(screen.getByText('作业已结束。')).toBeTruthy())
    expect(fieldValue(screen, '结束时间')).toBe('2026-11-17T03:00:00')
    // the refresh that reports the end reads the tail once more: the last lines show
    await waitFor(() =>
      expect(screen.getByTestId('log-content').textContent).toBe(
        'line 1\nline 2\ndone\n',
      ),
    )
    expect(ReadMemberFile).toHaveBeenCalledTimes(3)
    act(() => {
      jest.advanceTimersByTime(3 * JOBS_REFRESH_MS)
    })
    expect(GetMemberJob).toHaveBeenCalledTimes(3)
    expect(ReadMemberFile).toHaveBeenCalledTimes(3)
  })

  it('pauses the refresh while the page is hidden', async () => {
    jest.useFakeTimers()
    ;(ReadMemberFile as jest.Mock).mockResolvedValue(chunk('x\n'))
    renderPage()
    await waitFor(() => expect(GetMemberJob).toHaveBeenCalledTimes(1))
    setHidden(true)
    act(() => {
      jest.advanceTimersByTime(3 * JOBS_REFRESH_MS)
    })
    expect(GetMemberJob).toHaveBeenCalledTimes(1)
    setHidden(false)
    act(() => {
      jest.advanceTimersByTime(JOBS_REFRESH_MS)
    })
    await waitFor(() =>
      expect((GetMemberJob as jest.Mock).mock.calls.length).toBeGreaterThan(1),
    )
  })

  it('formats the last update as hh:mm:ss', () => {
    expect(clockTime(new Date(2026, 10, 17, 9, 5, 3).getTime())).toBe(
      '09:05:03',
    )
  })
})
