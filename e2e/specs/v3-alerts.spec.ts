// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
//
// v3 stuck-job alerts in two phases, so no browser session has to last for the whole idle window:
//   1. E2E_V3_ALERT_SUBMIT=<instance type>: submit `sleep E2E_V3_IDLE_SLEEP_S` (default 5400) from 提交作业 and leave
//      it running; the job id is written to <SHOTS_DIR>/alert-job.txt.
//   2. E2E_V3_ALERT_JOB=<job id> with E2E_V3_ALERT_KINDS (comma list of idle,long_run; default idle): sign in again,
//      check the badges in 我的作业 and the messages under 通知, then cancel the job with the one-click 取消
//      (E2E_V3_ALERT_CANCEL=1).
// Env: E2E_TEST_URL, E2E_MEMBER_EMAIL/PASSWORD, E2E_MEMBER_TEAM, E2E_CLUSTER, E2E_REGION, SHOTS_DIR.
import { expect, test } from '@playwright/test';
import { writeFileSync } from 'fs';
import { loginAs, TEAMS_CONFIG } from '../test-utils/teams';
import { api, openMenu, shot, SHOTS_DIR } from '../test-utils/v3';

const P = 'alerts'
const TEAM = TEAMS_CONFIG.memberTeam
const SUBMIT_TYPE = process.env.E2E_V3_ALERT_SUBMIT || ''
const JOB = process.env.E2E_V3_ALERT_JOB || ''
const KINDS = (process.env.E2E_V3_ALERT_KINDS || 'idle').split(',').map(k => k.trim()).filter(Boolean)
const SLEEP = Number(process.env.E2E_V3_IDLE_SLEEP_S || '5400')
const BADGE: Record<string, string> = { idle: '疑似空闲', long_run: '已运行超24小时' }
const BELL: Record<string, RegExp> = { idle: /GPU 几乎没有使用|GPU 利用率一直低于|GPU 疑似空闲/,
  long_run: /已运行 [\d.]+ 小时。(这只是提醒，)?作业会继续运行/ }

test.describe.configure({ mode: 'serial' })
test.describe('v3 alerts', () => {
  test.skip(!TEAMS_CONFIG.memberEmail || !TEAM, 'E2E_MEMBER_* not set')
  test.beforeEach(async ({ page }) => {
    await loginAs(page, TEAMS_CONFIG.memberEmail, TEAMS_CONFIG.memberPassword)
  })

  test('phase 1: submit an idle GPU job from 提交作业', async ({ page }) => {
    test.skip(!SUBMIT_TYPE, 'E2E_V3_ALERT_SUBMIT not set')
    await openMenu(page, '提交作业')
    await page.getByRole('row').filter({ hasText: SUBMIT_TYPE }).first().getByRole('radio').check()
    await page.locator('textarea').first().fill(`#!/bin/bash\n#SBATCH --output=%x-%j.out\nsleep ${SLEEP}\n`)
    await shot(page, P, '01-submit-form')
    await page.getByRole('button', { name: '提交', exact: true }).click()
    await page.waitForURL(/\/my-jobs\/\d+/)
    const jobId = page.url().split('/my-jobs/')[1].split(/[?#]/)[0]
    if (SHOTS_DIR) writeFileSync(`${SHOTS_DIR}/alert-job.txt`, `${jobId}\n`)
    await expect(page.getByText(/^最后更新：/).first()).toBeVisible({ timeout: 60000 })
    await shot(page, P, '02-job-submitted')
  })

  test('phase 2: badges in 我的作业, messages under 通知, one-click cancel', async ({ page }) => {
    test.skip(!JOB, 'E2E_V3_ALERT_JOB not set')
    const jobs = (await (await page.request.get(api('member/jobs', `&team=${TEAM}`))).json()).jobs as any[]
    const job = jobs.find(j => j.job_id === JOB)
    expect(job, `job ${JOB}`).toBeTruthy()
    for (const kind of KINDS) expect(job.alerts).toContain(kind)
    await openMenu(page, '我的作业')
    // the running-jobs row is the one with the 取消 button (history rows and the sticky header copy have none)
    const row = page.getByRole('button', { name: `取消作业 ${JOB}` }).locator('xpath=ancestor::tr[1]')
    for (const kind of KINDS) await expect(row).toContainText(BADGE[kind])
    await shot(page, P, '03-badges')
    await page.getByRole('button', { name: /^通知/ }).first().click()
    for (const kind of KINDS) await expect(page.getByText(BELL[kind]).first()).toBeVisible()
    await shot(page, P, '04-notifications')
    await page.keyboard.press('Escape')
    await page.goto(page.url().split('/pcui/')[0] + `/pcui/my-jobs/${JOB}`)
    await expect(page.getByText(/^最后更新：/).first()).toBeVisible({ timeout: 90000 })
    await shot(page, P, '05-job-detail-running')
    if (process.env.E2E_V3_ALERT_CANCEL === '1') {
      await openMenu(page, '我的作业')
      await page.getByRole('button', { name: `取消作业 ${JOB}` }).click()
      await page.getByRole('dialog').getByRole('button', { name: '确定取消' }).click()
      await expect(page.getByText(`作业 ${JOB} 已取消`)).toBeVisible()
      await shot(page, P, '06-cancelled')
    }
  })
})
