// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
//
// v2 team-lead checks: team jobs (metadata only), quota holders, environment management, admin routes denied.
// Env: E2E_TEST_URL, E2E_LEAD_EMAIL/PASSWORD (lead of E2E_V2_TEAM), E2E_V2_TEAM (default qa), E2E_OTHER_TEAM,
//      E2E_CLUSTER, E2E_REGION.
import { expect, test } from '@playwright/test';
import { baseUrl, csrfHeaders, loginAs, TEAMS_CONFIG, teamQuotaQuery } from '../test-utils/teams';
const TEAM = process.env.E2E_V2_TEAM || 'qa'
const FORBIDDEN_KEYS = /script|stdout|stderr|content|workdir|command/i
const api = (path: string, extra = '') => `${baseUrl()}/pcui/team-quota/${path}?${teamQuotaQuery()}${extra}`
function keys(value: unknown, out: string[] = []): string[] {
  if (Array.isArray(value)) value.forEach(v => keys(v, out))
  else if (value && typeof value === 'object')
    Object.entries(value).forEach(([k, v]) => { out.push(k); keys(v, out) })
  return out
}
test.describe.configure({ mode: 'serial' })
// Skipped since v3: these UI checks use the v2 English labels and the removed publish flow; the v3-admin,
// v3-lead and v3-member specs replace them.
test.describe.skip('v2 team lead', () => {
  test.skip(!TEAMS_CONFIG.leadEmail, 'E2E_LEAD_EMAIL not set')
  test.beforeEach(async ({ page }) => {
    await loginAs(page, TEAMS_CONFIG.leadEmail, TEAMS_CONFIG.leadPassword)
  })
  test('lead gets the team pages but no admin pages', async ({ page }) => {
    for (const link of ['Team quota', 'Team usage & cost', 'Team jobs', 'Quota holders', 'Environments'])
      await expect(page.getByRole('link', { name: link }).first()).toBeVisible()
    for (const link of ['Clusters', 'Users', 'Audit log'])
      await expect(page.getByRole('link', { name: link })).toHaveCount(0)
  })
  test('team jobs show metadata of every member and never script or output', async ({ page }) => {
    const response = await page.request.get(api('lead/jobs', `&team=${TEAM}`))
    expect(response.status()).toBe(200)
    const body = await response.json()
    expect(body.team).toBe(TEAM)
    expect(keys(body).filter(k => FORBIDDEN_KEYS.test(k))).toEqual([])
    await page.getByRole('link', { name: 'Team jobs' }).first().click()
    await expect(page.getByText(`Jobs of team ${TEAM}`)).toBeVisible({ timeout: 90000 })
  })
  test('quota holders list each instance type with the members holding machines', async ({ page }) => {
    const quota = await (await page.request.get(api('lead/quota', `&team=${TEAM}`))).json()
    expect(quota.team).toBe(TEAM)
    for (const type of quota.types) {
      const held = type.holders.reduce((sum: number, h: any) => sum + h.running, 0)
      expect(held).toBe(type.running)
    }
    await page.getByRole('link', { name: 'Quota holders' }).first().click()
    await expect(page.getByText(`Quota holders of team ${TEAM}`)).toBeVisible({ timeout: 90000 })
    await expect(page.locator('body')).not.toContainText(/billing|points/i)
  })
  test('lead may publish and delete team environments but not create them', async ({ page }) => {
    const list = await (await page.request.get(`${baseUrl()}/pcui/team-quota/member/envs?${teamQuotaQuery()}&team=${TEAM}`)).json()
    expect(list.roles.lead).toBe(true)
    for (const env of list.envs) expect(env.can.delete).toBe(true)
    for (const env of list.envs.filter((e: any) => e.status === 'succeeded' && !e.published))
      expect(env.can.publish).toBe(true)
    await page.getByRole('link', { name: 'Environments' }).first().click()
    await expect(page.getByText('Team environments')).toBeVisible({ timeout: 90000 })
    if (!list.roles.member)
      await expect(page.getByRole('button', { name: 'Create environment' })).toHaveCount(0)
  })
  test('lead is denied on admin routes and other teams', async ({ page }) => {
    const headers = await csrfHeaders(page)
    const q = teamQuotaQuery()
    const base = `${baseUrl()}/pcui/team-quota`
    const other = TEAMS_CONFIG.otherTeam
    const denied = [
      { method: 'GET', url: `${base}/audit` },
      { method: 'POST', url: `${base}/teams/${TEAM}/members/x/offboard?${q}`, data: { jobs: 'finish' } },
      { method: 'POST', url: `${base}/teams/${TEAM}/members/x/reenable?${q}`, data: {} },
      { method: 'PATCH', url: `${base}/teams/${TEAM}?${q}`, data: { budget_usd: 1 } },
      { method: 'GET', url: `${base}/lead/jobs?${q}&team=${other}` },
      { method: 'GET', url: `${base}/lead/quota?${q}&team=${other}` },
      { method: 'DELETE', url: `${base}/member/envs/shared/${TEAM}/x/1?${q}` },
    ]
    for (const call of denied) {
      const response = await page.request.fetch(call.url, { method: call.method, data: call.data, headers })
      expect(response.status(), `${call.method} ${call.url}`).toBe(403)
    }
  })
})
