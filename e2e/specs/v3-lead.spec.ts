// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
//
// v3 team-lead journey: 我的团队 tabs (概览 with specs and capacity status, 成员, 作业, 费用, 环境) and changing the
// visibility of a team environment (changed and restored).
// Env: E2E_TEST_URL, E2E_LEAD_EMAIL/PASSWORD (lead of E2E_V3_TEAM), E2E_V3_TEAM (default qa), E2E_CLUSTER,
//      E2E_REGION, E2E_V3_LEAD_ENV (optional: environment name of the team whose visibility is toggled and then
//      restored), SHOTS_DIR (optional: screenshots of every step).
import { expect, test } from '@playwright/test';
import { csrfHeaders, loginAs, TEAMS_CONFIG } from '../test-utils/teams';
import { api, menuLinks, openMenu, shot } from '../test-utils/v3';

const P = 'lead'
const TEAM = process.env.E2E_V3_TEAM || 'qa'
const ENV_NAME = process.env.E2E_V3_LEAD_ENV || ''

test.describe.configure({ mode: 'serial' })
test.describe('v3 team lead', () => {
  test.skip(!TEAMS_CONFIG.leadEmail, 'E2E_LEAD_EMAIL not set')
  test.beforeEach(async ({ page }) => {
    await loginAs(page, TEAMS_CONFIG.leadEmail, TEAMS_CONFIG.leadPassword)
  })

  test('menus: 我的团队 and 费用总览, no admin pages', async ({ page }) => {
    const links = await menuLinks(page)
    for (const name of ['我的团队', '费用总览']) expect(links).toContain(name)
    for (const name of ['团队管理', '机型与节点', '管理员账号', '审计日志']) expect(links).not.toContain(name)
    await shot(page, P, '01-menu')
  })

  test('我的团队 shows the tabs; 概览 has specs and capacity status', async ({ page }) => {
    await openMenu(page, '我的团队')
    await expect(page.getByText('机型与配额').first()).toBeVisible({ timeout: 90000 })
    await expect(page.getByText(/vCPU/).first()).toBeVisible()
    await expect(page.getByText(/有空闲|需启动约5分钟|暂无容量|全部在用，需排队/).first()).toBeVisible()
    await shot(page, P, '02-overview')
    for (const tab of ['成员', '作业', '费用', '环境']) {
      await page.getByRole('tab', { name: tab }).click()
      await page.waitForLoadState('networkidle')
      await shot(page, P, `03-${tab}`)
    }
    await expect(page.locator('body')).not.toContainText('发布')
  })

  test('changes the visibility of a team environment with the warning, then restores it', async ({ page }) => {
    test.skip(!ENV_NAME, 'E2E_V3_LEAD_ENV not set')
    const list = await (await page.request.get(api('member/envs', `&team=${TEAM}`))).json()
    const env = list.envs.find((e: any) => e.team === TEAM && e.name === ENV_NAME)
    expect(env, `environment ${ENV_NAME} of ${TEAM}`).toBeTruthy()
    const original = env.visibility as 'team' | 'all'
    const other = original === 'team' ? 'all' : 'team'
    await openMenu(page, '我的团队')
    await page.getByRole('tab', { name: '环境' }).click()
    await page.getByRole('button', { name: `更改 ${ENV_NAME} 的可见范围` }).first().click()
    const dialog = page.getByRole('dialog').filter({ hasText: '更改可见范围' })
    await dialog.getByText(other === 'all' ? '所有人' : '仅本团队', { exact: true }).click()
    await expect(dialog.getByRole('radio', { name: other === 'all' ? /所有人/ : /仅本团队/ })).toBeChecked()
    if (other === 'all') await expect(dialog.getByText('环境内容将对所有用户可读。')).toBeVisible()
    await shot(page, P, '04-visibility-dialog')
    await dialog.getByRole('button', { name: '保存' }).click()
    await expect(page.getByText(new RegExp(`${ENV_NAME} 的可见范围已改为`))).toBeVisible()
    await shot(page, P, '05-visibility-changed')
    // restore through the API so the environment is left as it was
    const headers = await csrfHeaders(page)
    const restore = await page.request.put(api(`member/envs/team/${TEAM}/${ENV_NAME}/visibility`, `&team=${TEAM}`),
      { headers, data: { visibility: original } })
    expect(restore.ok()).toBe(true)
  })
})
