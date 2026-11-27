import {act, fireEvent, render, waitFor} from '@testing-library/react'
import {AdvanceLifecycleTeam} from '../api'
import TeamProvisionModal, {IN_PROGRESS_WAIT_MS} from '../TeamProvisionModal'
import {TestProviders} from '../test-utils/fixtures'

jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  AdvanceLifecycleTeam: jest.fn(),
}))

const target = {clusterName: 'hpc-poc', region: 'us-west-2'}
const IDS = ['ad', 'storage', 'ecr', 'cognito', 'slurm'] as const

function response(statuses: string[], extra: Record<string, unknown> = {}) {
  return {
    team: 'teamc',
    state: 'provisioning',
    complete: statuses.every(s => s === 'done'),
    steps: IDS.map((id, i) => ({id, status: statuses[i]})),
    ...extra,
  }
}

function renderModal() {
  return render(
    <TestProviders>
      <TeamProvisionModal
        target={target}
        team="teamc"
        visible={true}
        onDismiss={jest.fn()}
      />
    </TestProviders>,
  )
}

describe('TeamProvisionModal', () => {
  afterEach(() => jest.useRealTimers())
  it('says the connection was lost (not that a step failed) when no answer reaches the browser', async () => {
    ;(AdvanceLifecycleTeam as jest.Mock).mockReset()
    ;(AdvanceLifecycleTeam as jest.Mock)
      .mockRejectedValueOnce(
        Object.assign(new Error('Network Error'), {isAxiosError: true}),
      )
      .mockResolvedValueOnce(response(['done', 'done', 'done', 'done', 'done']))
    const {findByText, queryByText, getByText} = renderModal()
    expect(await findByText('与门户的连接已断开')).toBeTruthy()
    expect(queryByText('有步骤失败')).toBeNull()
    expect(queryByText('Network Error')).toBeNull()
    fireEvent.click(getByText('重试'))
    await waitFor(() => expect(queryByText('与门户的连接已断开')).toBeNull())
  })

  it('advances step by step, waits while a step is in progress and completes', async () => {
    jest.useFakeTimers()
    const mock = AdvanceLifecycleTeam as jest.Mock
    mock
      .mockReset()
      .mockResolvedValueOnce(
        response(['done', 'pending', 'pending', 'pending', 'pending']),
      )
      .mockResolvedValueOnce(
        response(['done', 'done', 'in_progress', 'pending', 'pending']),
      )
      .mockResolvedValueOnce(
        response(['done', 'done', 'done', 'done', 'done'], {
          state: 'active',
        }),
      )
    const screen = renderModal()
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByText(/仍有步骤在执行/)).toBeTruthy())
    expect(mock).toHaveBeenCalledTimes(2)
    await act(async () => {
      jest.advanceTimersByTime(IN_PROGRESS_WAIT_MS)
    })
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(3))
    await waitFor(() =>
      expect(screen.getAllByText('团队 teamc 已就绪。').length).toBeGreaterThan(
        0,
      ),
    )
    expect(mock).toHaveBeenCalledWith(target, 'teamc')
  })

  it('stops on a failed step and retries on request', async () => {
    const mock = AdvanceLifecycleTeam as jest.Mock
    mock
      .mockReset()
      .mockResolvedValueOnce({
        ...response(['done', 'done', 'failed', 'pending', 'pending']),
        steps: IDS.map((id, i) => ({
          id,
          status: ['done', 'done', 'failed', 'pending', 'pending'][i],
          message: i === 2 ? 'AccessDenied: denied' : undefined,
        })),
      })
      .mockResolvedValueOnce(response(['done', 'done', 'done', 'done', 'done']))
    const screen = renderModal()
    await waitFor(() =>
      expect(screen.getByText('AccessDenied: denied')).toBeTruthy(),
    )
    expect(mock).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', {name: '重试'}))
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(screen.getAllByText('团队 teamc 已就绪。').length).toBeGreaterThan(
        0,
      ),
    )
  })
})
