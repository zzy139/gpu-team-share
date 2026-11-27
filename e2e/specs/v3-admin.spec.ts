// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
//
// v3 administrator journey (read only): Chinese menus, 机型与节点, team detail tabs, fairshare 高级选项 with the
// live share, 费用总览 explanation, 管理员账号, 审计日志 with env_visibility.
// Env: E2E_TEST_URL, E2E_ADMIN_EMAIL/PASSWORD, E2E_CLUSTER, E2E_REGION, E2E_V3_TEAM (team to open, default the
//      first team), SHOTS_DIR (optional: screenshots of every step).
import { expect, test } from '@playwright/test';
import { loginAs, TEAMS_CONFIG } from '../test-utils/teams';
import { api, menuLinks, openMenu, shot } from '../test-utils/v3';

const P = 'admin'
const ADMIN_MENU = ['集群', '机型与节点', '镜像', '团队管理', '费用总览', '管理员账号', '审计日志']

test.describe.configure({ mode: 'serial' })
test.describe('v3 admin', () => {
  test.skip(!TEAMS_CONFIG.adminEmail, 'E2E_ADMIN_EMAIL not set')
  test.beforeEach(async ({ page }) => {
    await loginAs(page, TEAMS_CONFIG.adminEmail, TEAMS_CONFIG.adminPassword)
  })

  test('menus are in Chinese and grouped under 集群', async ({ page }) => {
    const links = await menuLinks(page)
    for (const name of ADMIN_MENU) expect(links).toContain(name)
    for (const name of ['我的作业', '提交作业']) expect(links).not.toContain(name)
    await shot(page, P, '01-menu')
  })

  test('机型与节点 shows every type with specs, status, team quotas and node states', async ({ page }) => {
    const capacity = await (await page.request.get(api('capacity'))).json()
    expect(capacity.types.length).toBeGreaterThan(0)
    await openMenu(page, '机型与节点')
    await expect(page.getByRole('heading', { name: '机型与节点' }).first()).toBeVisible()
    await expect(page.getByText(capacity.types[0].instance_type).first()).toBeVisible({ timeout: 90000 })
    for (const header of ['各团队配额和用量', '节点状态']) await expect(page.getByText(header).first()).toBeVisible()
    await expect(page.getByText(/有空闲|需启动约5分钟|暂无容量|全部在用，需排队/).first()).toBeVisible()
    await shot(page, P, '02-capacity')
  })

  test('团队管理 opens the team detail with its tabs', async ({ page }) => {
    const teams = (await (await page.request.get(api('teams'))).json()).teams
    expect(teams.length).toBeGreaterThan(0)
    const team = process.env.E2E_V3_TEAM || teams[0].name
    await openMenu(page, '团队管理')
    await expect(page.getByRole('link', { name: team, exact: true }).first()).toBeVisible({ timeout: 90000 })
    await shot(page, P, '03-teams')
    await page.getByRole('link', { name: team, exact: true }).first().click()
    for (const tab of ['概览', '成员', '作业', '费用', '环境']) {
      await page.getByRole('tab', { name: tab }).click()
      await page.waitForLoadState('networkidle')
      await shot(page, P, `04-team-${tab}`)
    }
    await page.getByRole('tab', { name: '概览' }).click()
    await expect(page.getByText('机型与配额').first()).toBeVisible()
  })

  test('creating a team keeps fairshare under 高级选项 with the live share (cancelled, nothing created)', async ({ page }) => {
    await openMenu(page, '团队管理')
    await page.getByRole('button', { name: /创建团队/ }).first().click()
    const dialog = page.getByRole('dialog')
    await dialog.getByText('高级选项').first().click()
    await expect(dialog.getByText(/约占/).first()).toBeVisible()
    await shot(page, P, '05-fairshare')
    await dialog.getByRole('button', { name: '取消' }).click()
  })

  test('费用总览 explains billed and estimated cost', async ({ page }) => {
    await openMenu(page, '费用总览')
    await page.getByText('费用说明').first().click()
    await expect(page.getByText(/Cost Explorer/).first()).toBeVisible()
    await shot(page, P, '06-cost')
  })

  test('管理员账号 and 审计日志 (env_visibility entries listed when present)', async ({ page }) => {
    await openMenu(page, '管理员账号')
    await expect(page.getByRole('heading', { name: /管理员账号/ }).first()).toBeVisible()
    await shot(page, P, '07-admins')
    await openMenu(page, '审计日志')
    await page.waitForLoadState('networkidle')
    const audit = await page.request.get(api('audit', '&action=env_visibility'))
    if (audit.ok()) {
      const entries = ((await audit.json()).entries || []) as unknown[]
      if (entries.length) await expect(page.getByText('env_visibility').first()).toBeVisible()
    }
    await shot(page, P, '08-audit')
  })
})
