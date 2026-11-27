// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
//
// v2 administrator checks: per-instance-type quotas (no billing), team form, audit log, offboarding, page style.
// Env: E2E_TEST_URL, E2E_ADMIN_EMAIL/PASSWORD, E2E_CLUSTER, E2E_REGION,
//      E2E_V2_TEAM (team under test, default qa),
//      E2E_V2_OFFBOARD_USER (optional cluster user of E2E_V2_TEAM: offboard with "finish", then re-enable),
//      E2E_SHOTS_DIR (optional: save Clusters / Team quota / Usage / Members screenshots there).
import { expect, Page, request, test } from '@playwright/test';
import { baseUrl, csrfHeaders, loginAs, TEAMS_CONFIG, teamQuotaQuery } from '../test-utils/teams';
const TEAM = process.env.E2E_V2_TEAM || 'qa'
const OFFBOARD_USER = process.env.E2E_V2_OFFBOARD_USER || ''
const SHOTS = process.env.E2E_SHOTS_DIR || ''
const api = (path: string, extra = '') => `${baseUrl()}/pcui/team-quota/${path}?${teamQuotaQuery()}${extra}`
/** Every key of a JSON value, recursively. */
function keys(value: unknown, out: string[] = []): string[] {
  if (Array.isArray(value)) value.forEach(v => keys(v, out))
  else if (value && typeof value === 'object')
    Object.entries(value).forEach(([k, v]) => { out.push(k); keys(v, out) })
  return out
}
async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}
test.describe.configure({ mode: 'serial' })
// Skipped since v3: these UI checks use the v2 English labels and the removed publish flow; the v3-admin,
// v3-lead and v3-member specs replace them. The anonymous-denial API checks below still run.
test.describe.skip('v2 admin', () => {
  test.skip(!TEAMS_CONFIG.adminEmail, 'E2E_ADMIN_EMAIL not set')
  test.beforeEach(async ({ page }) => {
    await loginAs(page, TEAMS_CONFIG.adminEmail, TEAMS_CONFIG.adminPassword)
  })
  test('catalog lists one instance type per queue with GPUs, vCPUs and memory', async ({ page }) => {
    const catalog = await (await page.request.get(api('catalog'))).json()
    expect(catalog.types.length).toBeGreaterThan(0)
    for (const type of catalog.types) {
      expect(type.instance_type).toMatch(/^[a-z0-9]+\.[a-z0-9]+$/)
      expect(type.gpus).toBeGreaterThan(0)
      expect(type.vcpus).toBeGreaterThan(0)
      expect(type.memory_gib).toBeGreaterThan(0)
    }
    for (const queue of catalog.unmanaged_queues)
      expect(['multi_type', 'no_gpu', 'duplicate_type', 'unknown_type', 'invalid_name']).toContain(queue.reason)
  })
  test('team quota page shows machines per instance type and no billing anywhere', async ({ page }) => {
    const teams = await (await page.request.get(api('teams'))).json()
    expect(keys(teams).filter(k => /billing|points/i.test(k))).toEqual([])
    const usage = await (await page.request.get(api('usage', '&start=2026-11-01&end=2026-12-01'))).json()
    expect(keys(usage).filter(k => /billing|points/i.test(k))).toEqual([])
    await page.getByRole('link', { name: 'Clusters' }).first().click()
    await page.waitForLoadState('networkidle')
    await shot(page, 'clusters')
    await page.getByRole('link', { name: 'Team quota' }).click()
    await expect(page.getByRole('cell', { name: TEAM, exact: true })).toBeVisible({ timeout: 90000 })
    await expect(page.locator('body')).not.toContainText(/billing|points/i)
    await shot(page, 'team-quota')
    await page.getByRole('link', { name: 'Team usage & cost' }).click()
    await expect(page.getByText('Usage by instance type')).toBeVisible({ timeout: 90000 })
    await expect(page.getByText('Usage by member')).toBeVisible()
    await expect(page.locator('body')).not.toContainText(/billing|points/i)
    await shot(page, 'team-usage')
  })
  test('create form: per-type counts, budget, env cap, member hint, inline error and Advanced link-existing', async ({ page }) => {
    const types = (await (await page.request.get(api('catalog'))).json()).types
    await page.getByRole('link', { name: 'Team quota' }).click()
    await page.getByRole('button', { name: 'Create team' }).click()
    const dialog = page.getByRole('dialog').filter({ hasText: 'Create team' })
    for (const type of types)
      await expect(dialog.getByLabel(`Machines of ${type.instance_type}`)).toBeVisible()
    await expect(dialog.getByLabel('Monthly budget (USD, optional)')).toBeVisible()
    await expect(dialog).not.toContainText(/billing|points/i)
    const lifecycle = await (await page.request.get(api('lifecycle/teams'))).json()
    if (lifecycle.configured) {
      await expect(dialog.getByLabel('Environment storage cap (GB, optional)')).toBeVisible()
      await dialog.getByRole('button', { name: 'Add member' }).click()
      await expect(dialog.getByText(/完整邮箱，例如 zhangsan@example.com/)).toBeVisible()
      await dialog.getByLabel('Member 1 email').fill('zhangsan')
      await expect(dialog.getByText(/including '@'/)).toBeVisible()
      await expect(dialog.getByRole('checkbox', { name: 'Link existing AD user' })).toBeHidden()
      await dialog.getByRole('button', { name: 'Advanced' }).click()
      await expect(dialog.getByRole('checkbox', { name: 'Link existing AD user' })).not.toBeChecked()
      await expect(dialog.getByText('仅当 AD 中已有同名旧账号且需要沿用时勾选')).toBeVisible()
    }
    await dialog.getByRole('button', { name: 'Cancel' }).click()
  })
  test('members modal shows the member state and offboarding actions', async ({ page }) => {
    await page.getByRole('link', { name: 'Team quota' }).click()
    await page.getByRole('row', { name: new RegExp(`\\b${TEAM}\\b`) }).getByRole('radio').click()
    await page.getByRole('button', { name: 'Manage members' }).click()
    const dialog = page.getByRole('dialog').filter({ hasText: `Members of ${TEAM}` })
    await expect(dialog.getByText(/Active|Offboarded|Finishing jobs/).first()).toBeVisible({ timeout: 90000 })
    await shot(page, 'members')
    await dialog.getByRole('button', { name: 'Close' }).click()
  })
  test('offboard (let jobs finish) and re-enable a member', async ({ page }) => {
    test.skip(!OFFBOARD_USER, 'E2E_V2_OFFBOARD_USER not set')
    const headers = await csrfHeaders(page)
    const member = `teams/${TEAM}/members/${encodeURIComponent(OFFBOARD_USER)}`
    const off = await page.request.post(api(`${member}/offboard`), { headers, data: { jobs: 'finish' } })
    expect(off.status()).toBe(200)
    const offBody = await off.json()
    expect(['finishing', 'offboarded']).toContain(offBody.state)
    expect(offBody.steps.map((s: any) => s.id)).toEqual(expect.arrayContaining(['cognito', 'ad', 'slurm']))
    const teams = await (await page.request.get(api('teams', `&team=${TEAM}`))).json()
    expect(teams.teams[0].offboarded[OFFBOARD_USER]).toBeTruthy()
    const on = await page.request.post(api(`${member}/reenable`), { headers, data: {} })
    expect(on.status()).toBe(200)
    expect((await on.json()).state).toBe('active')
  })
  test('audit log lists the changes and filters by team', async ({ page }) => {
    await page.getByRole('link', { name: 'Audit log' }).click()
    await expect(page.getByRole('heading', { name: /Audit log/ })).toBeVisible({ timeout: 90000 })
    await page.getByRole('textbox', { name: 'Team' }).fill(TEAM)
    await page.getByRole('button', { name: 'Apply filters' }).click()
    const month = new Date().toISOString().slice(0, 7)
    const audit = await (await page.request.get(`${baseUrl()}/pcui/team-quota/audit?month=${month}&team=${TEAM}`)).json()
    expect(Array.isArray(audit.entries)).toBe(true)
    for (const entry of audit.entries) expect(entry.team).toBe(TEAM)
    if (audit.entries.length)
      await expect(page.getByRole('cell', { name: audit.entries[0].action }).first()).toBeVisible({ timeout: 90000 })
  })
})
test('anonymous is denied on the v2 admin routes', async () => {
  const context = await request.newContext()
  const q = teamQuotaQuery()
  const base = `${baseUrl()}/pcui/team-quota`
  for (const url of [`${base}/catalog?${q}`, `${base}/audit`, `${base}/lead/jobs?${q}&team=${TEAM}`,
    `${base}/lead/quota?${q}&team=${TEAM}`]) {
    const response = await context.get(url, { maxRedirects: 0 })
    expect(response.status(), url).toBe(401)
  }
  const post = await context.post(`${base}/teams/${TEAM}/members/x/offboard?${q}`, { data: { jobs: 'finish' }, maxRedirects: 0 })
  expect(post.status()).toBe(401)
  await context.dispose()
})
