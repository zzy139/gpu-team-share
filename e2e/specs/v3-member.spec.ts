// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
//
// v3 business-user journey: Chinese menus; 提交作业 without CPU / 内存 / GPU / 时长 fields, with specs, capacity
// status, the collapsed 其他机型（仅可借用） and the 4-hour borrow notice and cap; environment visibility with the
// warning; another team's 仅本团队 environment refused by the server; job detail while pending (information, no
// red error, 结束时间 "-", auto-refresh with 最后更新, Chinese 排队原因); 我的作业 auto-refresh; GPU idle alert badge;
// e-mail alert setting.
// Env: E2E_TEST_URL, E2E_MEMBER_EMAIL/PASSWORD (member of E2E_MEMBER_TEAM), E2E_MEMBER_TEAM, E2E_CLUSTER, E2E_REGION,
//      SHOTS_DIR (optional: screenshots of every step),
//      E2E_V3_TYPE (own-quota instance type for the pending-job checks, default the first type with quota),
//      E2E_V3_SUBMIT=1 (optional: submit a short job, check the detail page while it waits, then cancel it),
//      E2E_V3_PRIVATE_ENV=<team>/<name>/<version> (optional: a 仅本团队 environment of another team),
//      E2E_V3_IDLE=1 with E2E_V3_IDLE_TYPE (optional: `sleep 4000` on a GPU type until the 疑似空闲 badge shows,
//      then cancel; E2E_V3_IDLE_TIMEOUT_MIN, default 80; E2E_V3_IDLE_SLEEP_S, default 4000).
import { expect, Page, test } from '@playwright/test';
import { csrfHeaders, loginAs, TEAMS_CONFIG } from '../test-utils/teams';
import { api, lastUpdated, menuLinks, openMenu, shot } from '../test-utils/v3';

const P = 'member'
const TEAM = TEAMS_CONFIG.memberTeam
const SUBMIT = process.env.E2E_V3_SUBMIT === '1'
const PRIVATE_ENV = process.env.E2E_V3_PRIVATE_ENV || ''
const IDLE = process.env.E2E_V3_IDLE === '1'
const IDLE_TYPE = process.env.E2E_V3_IDLE_TYPE || ''
const IDLE_TIMEOUT = Number(process.env.E2E_V3_IDLE_TIMEOUT_MIN || '80') * 60 * 1000
const IDLE_SLEEP = Number(process.env.E2E_V3_IDLE_SLEEP_S || '4000')
const REFRESH_MS = 15000
const member = (path: string, extra = '') => api(`member/${path}`, `&team=${TEAM}${extra}`)

async function ownTypes(page: Page) {
  const options = await (await page.request.get(member('options'))).json()
  return options.types as any[]
}
async function cancelJob(page: Page, jobId: string) {
  const headers = await csrfHeaders(page)
  await page.request.post(member(`jobs/${jobId}/cancel`), { headers, data: {} })
}
async function submitFromForm(page: Page, type: string, script: string) {
  await openMenu(page, '提交作业')
  await page.getByRole('row').filter({ hasText: type }).first().getByRole('radio').check()
  await page.locator('textarea').first().fill(script)
  await page.getByRole('button', { name: '提交', exact: true }).click()
  await page.waitForURL(/\/my-jobs\/\d+/)
  return page.url().split('/my-jobs/')[1].split(/[?#]/)[0]
}

test.describe.configure({ mode: 'serial' })
test.describe('v3 member', () => {
  test.skip(!TEAMS_CONFIG.memberEmail || !TEAM, 'E2E_MEMBER_* not set')
  test.beforeEach(async ({ page }) => {
    await loginAs(page, TEAMS_CONFIG.memberEmail, TEAMS_CONFIG.memberPassword)
  })

  test('menus are in Chinese, without admin pages', async ({ page }) => {
    const links = await menuLinks(page)
    for (const name of ['我的作业', '提交作业', '环境', '我的团队']) expect(links).toContain(name)
    for (const name of ['团队管理', '机型与节点', '审计日志', '管理员账号']) expect(links).not.toContain(name)
    await shot(page, P, '01-menu')
  })

  test('提交作业: specs and status, no CPU / 内存 / GPU / 时长 fields, borrow-only types collapsed', async ({ page }) => {
    const types = await ownTypes(page)
    await openMenu(page, '提交作业')
    for (const header of ['GPU 型号', '每块 GPU 显存', 'vCPU', '内存', '团队上限', '运行中', '剩余', '状态'])
      await expect(page.getByRole('columnheader', { name: header }).first()).toBeVisible()
    for (const label of ['CPU 核数', '内存 (GB)', '每台 GPU 数（可选）', '时长上限（分钟）'])
      await expect(page.getByLabel(label)).toHaveCount(0)
    await expect(page.getByText(/每台 .+：.+作业独占整台机器。/).first()).toBeVisible()
    await expect(page.getByRole('tab', { name: '我的模板' })).toBeVisible()
    await shot(page, P, '02-submit')
    if (types.some(type => !type.has_quota)) {
      const section = page.getByRole('button', { name: /其他机型（仅可借用）/ }).first()
      await expect(section).toHaveAttribute('aria-expanded', 'false')
      await section.click()
      await expect(page.getByText('只借用其他团队的空闲配额，最长运行 4 小时，可能排队', { exact: false }).first())
        .toBeVisible()
      await shot(page, P, '03-borrow-only')
    }
  })

  test('borrowing shows the 4-hour notice and the request is capped at 240 minutes (request not sent)', async ({ page }) => {
    await openMenu(page, '提交作业')
    await page.getByText('团队配额已满时借用空闲资源（--qos=borrow）').click()
    await expect(page.getByText('借用作业最长运行 4 小时，到时会被结束。')).toBeVisible()
    await page.getByText('高级选项').first().click()
    await page.getByLabel('最长运行时间（分钟，可选）').fill('600')
    await expect(page.getByText('超过 240 分钟，将按 240 分钟提交。')).toBeVisible()
    await shot(page, P, '04-borrow')
    let body: any = null
    await page.route('**/team-quota/member/jobs?**', async route => {
      if (route.request().method() === 'POST') {
        body = route.request().postDataJSON()
        await route.abort()
      } else await route.continue()
    })
    await page.getByRole('button', { name: '提交', exact: true }).click()
    await expect.poll(() => body).not.toBeNull()
    expect(body.borrow).toBe(true)
    expect(body.time_minutes).toBe(240)
    for (const field of ['cpus', 'mem_gb', 'gpus_per_node']) expect(body).not.toHaveProperty(field)
  })

  test('environments: one list with filter and search; creating asks 仅本团队 / 所有人 with the warning (cancelled)', async ({ page }) => {
    await openMenu(page, '环境')
    await expect(page.getByText('检测到')).toHaveCount(0)
    await expect(page.getByRole('columnheader', { name: '可见范围' }).first()).toBeVisible()
    await expect(page.getByPlaceholder('搜索名称、版本、说明或创建人')).toBeVisible()
    await shot(page, P, '05-envs')
    await page.getByRole('button', { name: '创建环境' }).first().click()
    const dialog = page.getByRole('dialog').filter({ hasText: '可见范围' })
    await expect(dialog.getByRole('radio', { name: /仅本团队/ })).toBeChecked()
    await dialog.getByText('所有人', { exact: true }).click()
    await expect(dialog.getByRole('radio', { name: /所有人/ })).toBeChecked()
    await expect(dialog.getByText('环境内容将对所有用户可读。')).toBeVisible()
    await shot(page, P, '06-env-create-visibility')
    await dialog.getByRole('button', { name: '取消' }).click()
  })

  test('another team\'s 仅本团队 environment is refused by the server', async ({ page }) => {
    test.skip(!PRIVATE_ENV, 'E2E_V3_PRIVATE_ENV not set')
    const [team, name, version] = PRIVATE_ENV.split('/')
    const types = (await ownTypes(page)).filter(type => type.has_quota)
    const headers = await csrfHeaders(page)
    const response = await page.request.post(member('jobs'), {
      headers,
      data: {
        team: TEAM, instance_type: types[0].instance_type, nodes: 1, borrow: false,
        env: { scope: 'team', team, name, version }, script_content: '#!/bin/bash\ntrue\n',
      },
    })
    expect(response.status()).toBe(403)
    expect(JSON.stringify(await response.json())).toMatch(/仅对其(所属)?团队可见|不可用/)
  })

  test('job detail while waiting: information instead of an error, 结束时间 "-", auto-refresh, Chinese reason', async ({ page }) => {
    test.skip(!SUBMIT, 'E2E_V3_SUBMIT not set')
    test.setTimeout(10 * 60 * 1000)
    const types = (await ownTypes(page)).filter(type => type.has_quota)
    const type = process.env.E2E_V3_TYPE || types[0].instance_type
    const jobId = await submitFromForm(page, type, '#!/bin/bash\n#SBATCH --output=%x-%j.out\nsleep 120\n')
    try {
      await expect(page.getByText(/^最后更新：/).first()).toBeVisible({ timeout: 60000 })
      const state = (await (await page.request.get(member(`jobs/${jobId}`))).json()).state as string
      if (/^(PENDING|CONFIGURING)/.test(state)) {
        await expect(page.getByText('作业尚未开始运行，节点启动后会生成输出文件（约 5 分钟）。')).toBeVisible()
        await expect(page.getByText('找不到输出文件')).toHaveCount(0)
        const end = page.getByText('结束时间', { exact: true }).first().locator('xpath=..')
        await expect(end).toContainText('-')
        const reason = page.getByText('排队原因', { exact: true }).first().locator('xpath=..')
        await expect(reason).toContainText(/[\u4e00-\u9fff]/)
      }
      await shot(page, P, '07-job-detail')
      const before = await lastUpdated(page)
      await page.waitForTimeout(REFRESH_MS + 3000)
      await expect.poll(() => lastUpdated(page), { timeout: 30000 }).not.toBe(before)
      await shot(page, P, '08-job-detail-refreshed')
      await openMenu(page, '我的作业')
      await expect(page.getByRole('link', { name: jobId })).toBeVisible()
      const listBefore = await lastUpdated(page)
      await page.waitForTimeout(REFRESH_MS + 3000)
      await expect.poll(() => lastUpdated(page), { timeout: 30000 }).not.toBe(listBefore)
      await shot(page, P, '09-my-jobs-refreshed')
    } finally {
      await cancelJob(page, jobId)
    }
  })

  test('a GPU job that stays idle gets the 疑似空闲 badge', async ({ page }) => {
    test.skip(!IDLE || !IDLE_TYPE, 'E2E_V3_IDLE / E2E_V3_IDLE_TYPE not set')
    test.setTimeout(IDLE_TIMEOUT + 10 * 60 * 1000)
    const jobId = await submitFromForm(page, IDLE_TYPE, `#!/bin/bash\n#SBATCH --output=%x-%j.out\nsleep ${IDLE_SLEEP}\n`)
    try {
      await expect
        .poll(async () => {
          const jobs = (await (await page.request.get(member('jobs'))).json()).jobs as any[]
          return jobs.find(job => job.job_id === jobId)?.alerts || []
        }, { timeout: IDLE_TIMEOUT, intervals: [60000] })
        .toContain('idle')
      await openMenu(page, '我的作业')
      await expect(page.getByRole('row').filter({ hasText: jobId }).getByText('疑似空闲')).toBeVisible()
      await shot(page, P, '10-idle-badge')
      await page.getByRole('button', { name: /^通知/ }).first().click()
      await expect(page.getByText('GPU 疑似空闲').first()).toBeVisible()
      await shot(page, P, '11-idle-notification')
    } finally {
      await cancelJob(page, jobId)
    }
  })

  test('通知设置 offers the e-mail alerts with the confirmation hint (not changed)', async ({ page }) => {
    await openMenu(page, '我的作业')
    await page.getByRole('button', { name: '通知设置' }).click()
    const dialog = page.getByRole('dialog').filter({ hasText: '通知设置' })
    await expect(dialog.getByText('邮件提醒（GPU 空闲 / 运行时间长 / 暂无容量）')).toBeVisible()
    await shot(page, P, '12-preferences')
    await expect(dialog.getByText(/邮件通知已关闭|请在发往 .+ 的邮件中确认订阅|邮件会发送到/).first()).toBeVisible()
    if (process.env.E2E_V3_EMAIL === '1' && await dialog.getByText(/邮件通知已关闭/).count()) {
      // First change of any switch creates the e-mail subscription: turn 邮件提醒 off and on again.
      const alerts = dialog.locator('input[type="checkbox"]').nth(3) // job_notify, email_jobs, email_budget, email_alerts
      for (const want of [false, true]) {
        await dialog.getByText('邮件提醒（GPU 空闲 / 运行时间长 / 暂无容量）').click()
        await expect(alerts).toBeChecked({ checked: want })
        await expect(alerts).toBeEnabled()
      }
      await expect(dialog.getByText(/请在发往 .+ 的邮件中确认订阅。/)).toBeVisible()
      await shot(page, P, '13-preferences-email-pending')
    }
    await dialog.getByRole('button', { name: '关闭' }).click()
  })
})
