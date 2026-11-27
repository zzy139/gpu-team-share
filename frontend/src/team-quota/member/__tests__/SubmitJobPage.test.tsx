import wrapper from '@cloudscape-design/components/test-utils/dom'
import {fireEvent, render, waitFor} from '@testing-library/react'
import {Route, Routes} from 'react-router-dom'
import {clearAllState, setState} from '../../../store'
import {TestProviders} from '../../test-utils/fixtures'
import {
  CreateTemplate,
  GetMemberMe,
  GetMemberOptions,
  GetResubmit,
  ListTemplates,
  SubmitMemberJob,
} from '../api'
import SubmitJobPage from '../SubmitJobPage'
import {me, memberIdentity, memberTarget, options} from '../test-utils/fixtures'
jest.mock('../api', () => ({
  ...(jest.requireActual('../api') as any),
  GetMemberBudget: jest.fn().mockResolvedValue({team: 'teama', budget: null}),
  ListNotifications: jest.fn().mockResolvedValue({items: [], unread: 0}),
  GetMemberMe: jest.fn(),
  GetMemberOptions: jest.fn(),
  SubmitMemberJob: jest.fn(),
  GetResubmit: jest.fn(),
  ListTemplates: jest.fn(),
  CreateTemplate: jest.fn(),
}))
const template = {
  id: 't1',
  name: 'nightly',
  job: {
    instance_type: 'g6e.xlarge',
    nodes: 2,
    borrow: false,
    env: null,
    job_name: 'nightly',
    script_content: '#!/bin/bash\necho nightly\n',
  },
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-02T00:00:00Z',
}
async function renderPage(path = '/submit', waitForText = 'g6e.12xlarge') {
  const screen = render(
    <TestProviders path={path}>
      <Routes>
        <Route path="/submit" element={<SubmitJobPage />} />
      </Routes>
    </TestProviders>,
  )
  await waitFor(() =>
    expect(GetMemberOptions).toHaveBeenCalledWith(memberTarget),
  )
  await waitFor(() => expect(screen.getByText(waitForText)).toBeTruthy())
  return screen
}
function submit(screen: any) {
  fireEvent.click(screen.getByRole('button', {name: '提交'}))
}
function lastBody() {
  const calls = (SubmitMemberJob as jest.Mock).mock.calls
  return calls[calls.length - 1][1]
}
function selectType(screen: any, type: string) {
  const row = screen.getByText(type).closest('tr')!
  fireEvent.click(row.querySelector('input[type="radio"]')!)
}
function openSection(screen: any, title: string) {
  // the header text is also rendered once more for the region label
  fireEvent.click(screen.getAllByText(title)[0])
}
describe('SubmitJobPage', () => {
  beforeEach(() => {
    clearAllState()
    setState(['identity'], memberIdentity)
    ;(GetMemberMe as jest.Mock).mockResolvedValue(me)
    ;(GetMemberOptions as jest.Mock).mockResolvedValue(options)
    ;(ListTemplates as jest.Mock).mockResolvedValue({templates: [], max: 50})
    ;(SubmitMemberJob as jest.Mock)
      .mockReset()
      .mockResolvedValue({job_id: '13', script_path: '/efs/teama/x'})
  })
  it('shows the specs, quota and capacity status of each own type, and no CPU, memory, GPU or time fields', async () => {
    const screen = await renderPage()
    for (const header of [
      'GPU 型号',
      '每块 GPU 显存',
      'vCPU',
      '内存',
      '团队上限',
      '运行中',
      '剩余',
      '状态',
    ])
      expect(screen.getAllByText(header).length).toBeGreaterThan(0)
    expect(screen.getByText('4× NVIDIA L40S')).toBeTruthy()
    expect(screen.getByText('384 GiB')).toBeTruthy()
    expect(screen.getByText('需启动约5分钟')).toBeTruthy()
    expect(screen.getByText('全部在用，需排队')).toBeTruthy()
    expect(
      screen.getByText(
        '每个作业独占整台机器。请选择能满足作业需要的最小机型。',
      ),
    ).toBeTruthy()
    // the selected type's specs replace the CPU / memory / GPU fields
    expect(
      screen.getByText(
        '每台 g6e.12xlarge：4× NVIDIA L40S 48 GB · 48 vCPU · 384 GiB。作业独占整台机器。',
      ),
    ).toBeTruthy()
    for (const label of [
      'CPU 核数',
      '内存 (GB)',
      '每台 GPU 数（可选）',
      '时长上限（分钟）',
    ])
      expect(screen.queryByLabelText(label)).toBeNull()
    // the multi-type queue is announced, not offered
    expect(screen.getByText(/队列 mixed 包含多种机型/)).toBeTruthy()
    expect(screen.queryByText('g6.xlarge')).toBeNull()
  })
  it('submits whole machines of the type without CPU, memory, GPU or time limit', async () => {
    const screen = await renderPage()
    fireEvent.change(document.querySelector('textarea')!, {
      target: {value: '#!/bin/bash\necho hi\n'},
    })
    submit(screen)
    await waitFor(() => expect(SubmitMemberJob).toHaveBeenCalled())
    const body = lastBody()
    expect(body).toEqual({
      team: 'teama',
      instance_type: 'g6e.12xlarge',
      nodes: 1,
      borrow: false,
      env: null,
      script_content: '#!/bin/bash\necho hi\n',
    })
    for (const field of [
      'cpus',
      'mem_gb',
      'gpus_per_node',
      'time_minutes',
      'partition',
    ])
      expect(body).not.toHaveProperty(field)
  })
  it('sends the chosen type, machines and an environment of another team', async () => {
    const screen = await renderPage()
    selectType(screen, 'g6e.xlarge')
    fireEvent.change(screen.getByLabelText('机器数量'), {
      target: {value: '2'},
    })
    // no templates and one team: the only select is the environment
    const env = wrapper(screen.container).findSelect()!
    env.openDropdown()
    env.selectOptionByValue('team/teamb/alphafold/2.3')
    submit(screen)
    await waitFor(() => expect(SubmitMemberJob).toHaveBeenCalled())
    expect(lastBody()).toMatchObject({
      instance_type: 'g6e.xlarge',
      nodes: 2,
      env: {scope: 'team', team: 'teamb', name: 'alphafold', version: '2.3'},
    })
    expect(lastBody()).not.toHaveProperty('gpus_per_node')
  })
  it('keeps types without own quota in a collapsed borrow-only section; choosing one forces borrow and the 4-hour cap', async () => {
    const screen = await renderPage()
    expect(screen.getAllByText('其他机型（仅可借用）').length).toBeGreaterThan(
      0,
    )
    expect(
      screen.getByText(
        '本团队没有这些机型的配额。只借用其他团队的空闲配额，最长运行 4 小时，可能排队。',
      ),
    ).toBeTruthy()
    // collapsed (Cloudscape keeps the content in the DOM, hidden)
    const toggle = () =>
      screen
        .getAllByText('其他机型（仅可借用）')[0]
        .closest('[aria-expanded]')!
        .getAttribute('aria-expanded')
    expect(toggle()).toBe('false')
    openSection(screen, '其他机型（仅可借用）')
    expect(toggle()).toBe('true')
    expect(screen.getByText('p5.48xlarge')).toBeTruthy()
    expect(screen.getByText('暂无容量')).toBeTruthy()
    selectType(screen, 'p5.48xlarge')
    expect(
      screen.getByText('本团队没有这个机型的配额，只能以借用方式运行。'),
    ).toBeTruthy()
    expect(
      screen.getByText('借用作业最长运行 4 小时，到时会被结束。'),
    ).toBeTruthy()
    submit(screen)
    await waitFor(() => expect(SubmitMemberJob).toHaveBeenCalled())
    expect(lastBody()).toMatchObject({
      instance_type: 'p5.48xlarge',
      borrow: true,
      time_minutes: 240,
    })
  })
  it('borrowing shows the 4-hour notice and caps a longer advanced limit at 240 minutes', async () => {
    const screen = await renderPage()
    expect(
      screen.queryByText('借用作业最长运行 4 小时，到时会被结束。'),
    ).toBeNull()
    fireEvent.click(
      screen.getByText('团队配额已满时借用空闲资源（--qos=borrow）'),
    )
    expect(
      screen.getByText('借用作业最长运行 4 小时，到时会被结束。'),
    ).toBeTruthy()
    openSection(screen, '高级选项')
    fireEvent.change(screen.getByLabelText('最长运行时间（分钟，可选）'), {
      target: {value: '600'},
    })
    expect(screen.getByText('超过 240 分钟，将按 240 分钟提交。')).toBeTruthy()
    submit(screen)
    await waitFor(() => expect(SubmitMemberJob).toHaveBeenCalled())
    expect(lastBody()).toMatchObject({borrow: true, time_minutes: 240})
  })
  it('borrowing keeps a shorter advanced limit', async () => {
    const screen = await renderPage()
    fireEvent.click(
      screen.getByText('团队配额已满时借用空闲资源（--qos=borrow）'),
    )
    openSection(screen, '高级选项')
    fireEvent.change(screen.getByLabelText('最长运行时间（分钟，可选）'), {
      target: {value: '90'},
    })
    submit(screen)
    await waitFor(() => expect(SubmitMemberJob).toHaveBeenCalled())
    expect(lastBody()).toMatchObject({borrow: true, time_minutes: 90})
  })
  it('sends a time limit for an own-quota job only when it is set in 高级选项', async () => {
    const screen = await renderPage()
    openSection(screen, '高级选项')
    const minutes = screen.getByLabelText('最长运行时间（分钟，可选）')
    fireEvent.change(minutes, {target: {value: '0'}})
    submit(screen)
    expect(SubmitMemberJob).not.toHaveBeenCalled()
    expect(screen.getByText('请输入 1 到 10080 之间的整数')).toBeTruthy()
    fireEvent.change(minutes, {target: {value: '720'}})
    submit(screen)
    await waitFor(() => expect(SubmitMemberJob).toHaveBeenCalled())
    expect(lastBody()).toMatchObject({borrow: false, time_minutes: 720})
  })
  it('submits a script path only', async () => {
    const screen = await renderPage()
    fireEvent.click(screen.getByLabelText('脚本路径'))
    fireEvent.change(screen.getAllByLabelText('脚本路径').pop()!, {
      target: {value: '/efs/teama/run.sh'},
    })
    submit(screen)
    await waitFor(() => expect(SubmitMemberJob).toHaveBeenCalled())
    const body = lastBody()
    expect(body.script_path).toBe('/efs/teama/run.sh')
    expect(body).not.toHaveProperty('script_content')
  })
  it('blocks scripts larger than 64 KiB', async () => {
    const screen = await renderPage()
    fireEvent.change(document.querySelector('textarea')!, {
      target: {value: 'x'.repeat(65537)},
    })
    submit(screen)
    expect(SubmitMemberJob).not.toHaveBeenCalled()
    expect(
      screen.getByText('脚本不能超过 65536 字节，请改用脚本路径'),
    ).toBeTruthy()
  })
  it('prefills the form from an earlier job and drops CPU, memory and GPUs per machine', async () => {
    ;(GetResubmit as jest.Mock).mockResolvedValue({
      job_id: '10',
      partition: 'g6e-xlarge',
      job: {
        instance_type: 'g6e.xlarge',
        nodes: 1,
        gpus_per_node: 1,
        cpus: 4,
        mem_gb: 16,
        time_minutes: 30,
        borrow: false,
        env: {scope: 'team', team: 'teama', name: 'cuda', version: '12.4'},
        job_name: 'old',
        script_content: '#!/bin/bash\necho again\n',
      },
    })
    const screen = await renderPage('/submit?resubmit=10')
    await waitFor(() =>
      expect(GetResubmit).toHaveBeenCalledWith(memberTarget, '10'),
    )
    await waitFor(() =>
      expect(document.querySelector('textarea')!.value).toBe(
        '#!/bin/bash\necho again\n',
      ),
    )
    // the earlier time limit is shown in the opened advanced options
    expect(
      (screen.getByLabelText('最长运行时间（分钟，可选）') as HTMLInputElement)
        .value,
    ).toBe('30')
    submit(screen)
    await waitFor(() => expect(SubmitMemberJob).toHaveBeenCalled())
    const body = lastBody()
    expect(body).toMatchObject({
      instance_type: 'g6e.xlarge',
      time_minutes: 30,
      job_name: 'old',
      env: {scope: 'team', team: 'teama', name: 'cuda', version: '12.4'},
    })
    for (const field of ['cpus', 'mem_gb', 'gpus_per_node'])
      expect(body).not.toHaveProperty(field)
  })
  it('saves the form as a template', async () => {
    ;(CreateTemplate as jest.Mock).mockResolvedValue({id: 'x'})
    const screen = await renderPage()
    fireEvent.click(screen.getByRole('button', {name: '另存为模板'}))
    fireEvent.change(screen.getByLabelText('模板名称'), {
      target: {value: 'nightly'},
    })
    fireEvent.click(screen.getByRole('button', {name: '保存'}))
    await waitFor(() => expect(CreateTemplate).toHaveBeenCalled())
    const [, body] = (CreateTemplate as jest.Mock).mock.calls[0]
    expect(body.name).toBe('nightly')
    expect(body.job).toMatchObject({instance_type: 'g6e.12xlarge', nodes: 1})
    expect(body.job).not.toHaveProperty('team')
    expect(body.job).not.toHaveProperty('time_minutes')
  })
  it('lists the templates in the 我的模板 tab and fills the form from one', async () => {
    ;(ListTemplates as jest.Mock).mockResolvedValue({
      templates: [template],
      max: 50,
    })
    const screen = await renderPage('/submit?tab=templates', 'nightly')
    expect(screen.getByRole('tab', {name: '我的模板'})).toBeTruthy()
    fireEvent.click(screen.getByRole('button', {name: '使用'}))
    await waitFor(() => expect(screen.getByText('g6e.12xlarge')).toBeTruthy())
    await waitFor(() =>
      expect(document.querySelector('textarea')!.value).toBe(
        '#!/bin/bash\necho nightly\n',
      ),
    )
    expect((screen.getByLabelText('机器数量') as HTMLInputElement).value).toBe(
      '2',
    )
    submit(screen)
    await waitFor(() => expect(SubmitMemberJob).toHaveBeenCalled())
    expect(lastBody()).toMatchObject({
      instance_type: 'g6e.xlarge',
      nodes: 2,
      job_name: 'nightly',
    })
  })
})
