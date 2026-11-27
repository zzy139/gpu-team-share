// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
//
// v2 access-scope checks: a lead sees only the own team, anonymous and out-of-scope calls are denied,
// a member cannot reach another team directory (also not through a symlink).
// Env: E2E_TEST_URL, E2E_LEAD_EMAIL/PASSWORD (lead of E2E_V2_TEAM), E2E_V2_TEAM (default qa),
//      E2E_MEMBER_EMAIL/PASSWORD (member of E2E_MEMBER_TEAM), E2E_MEMBER_TEAM, E2E_OTHER_TEAM (default teamb),
//      E2E_V2_TYPE (instance type, default g6e.xlarge), E2E_CLUSTER, E2E_REGION.
import { expect, Page, request, test } from '@playwright/test';
import { baseUrl, csrfHeaders, loginAs, TEAMS_CONFIG, teamQuotaQuery } from '../test-utils/teams';
const TEAM = process.env.E2E_V2_TEAM || 'qa'
const TYPE = process.env.E2E_V2_TYPE || 'g6e.xlarge'
const TEST_TEAM = 'qatest'
const JOB_TIMEOUT = 20 * 60 * 1000
const teamBody = { name: TEST_TEAM, fairshare: 1, type_limits: { [TYPE]: 1 } }
const jobBody = (script: string) => ({ instance_type: TYPE, nodes: 1, time_minutes: 5, script_content: script })
function memberUrl(path: string, extra = '') {
  return `${baseUrl()}/pcui/team-quota/member/${path}?${teamQuotaQuery()}&team=${TEAMS_CONFIG.memberTeam}${extra}`
}
/** Calls that neither a lead (team-* group only) nor an anonymous caller may make. */
function deniedCalls() {
  const q = teamQuotaQuery()
  const base = `${baseUrl()}/pcui`
  const other = TEAMS_CONFIG.otherTeam
  return [
    { method: 'GET', url: `${base}/team-quota/teams?${q}&team=${other}` },
    { method: 'GET', url: `${base}/team-quota/usage?${q}&team=${other}&start=2026-01-01&end=2026-02-01` },
    { method: 'PATCH', url: `${base}/team-quota/teams/${other}?${q}`, data: { type_limits: { [TYPE]: 1 } } },
    { method: 'POST', url: `${base}/team-quota/teams?${q}`, data: teamBody },
    { method: 'GET', url: `${base}/manager/list_users` },
    { method: 'GET', url: `${base}/api?path=/v3/clusters` },
    { method: 'GET', url: `${base}/team-quota/lifecycle/teams?${q}` },
    { method: 'POST', url: `${base}/team-quota/lifecycle/teams?${q}`, data: teamBody },
    { method: 'POST', url: `${base}/team-quota/lifecycle/teams/${TEST_TEAM}/advance?${q}`, data: {} },
    { method: 'POST', url: `${base}/team-quota/teams/${other}/members?${q}`, data: { email: 'qauser@example.com' } },
    // A lead (team-* group only) is not a member: the member portal is denied too.
    { method: 'GET', url: `${base}/team-quota/member/jobs?${q}&team=${TEAM}` },
    { method: 'POST', url: `${base}/team-quota/member/jobs?${q}&team=${TEAM}`, data: jobBody('#!/bin/bash\ntrue\n') },
    { method: 'GET', url: `${base}/manager/queue_status?${q}` },
  ]
}
async function teamRoot(page: Page) {
  const me = await (await page.request.get(`${baseUrl()}/pcui/team-quota/member/me`)).json()
  const root: string = me.teams.find((t: any) => t.team === TEAMS_CONFIG.memberTeam).team_path
  return { root, otherRoot: root.replace(/[^/]+$/, TEAMS_CONFIG.otherTeam) }
}
test.describe.configure({ mode: 'serial' })
test.describe('v2 lead scope', () => {
  test.skip(!TEAMS_CONFIG.leadEmail, 'E2E_LEAD_EMAIL not set')
  test.beforeEach(async ({ page }) => {
    await loginAs(page, TEAMS_CONFIG.leadEmail, TEAMS_CONFIG.leadPassword)
  })
  test('lead sees only own team', async ({ page }) => {
    // v3: a lead lands on 我的团队; 费用总览 lists the own team only, without the cluster totals
    await expect(page).toHaveURL(/\/pcui\/my-team/)
    await page.getByRole('link', { name: '费用总览', exact: true }).first().click()
    await expect(page.getByRole('cell', { name: TEAM, exact: true }).first()).toBeVisible({ timeout: 90000 })
    await expect(page.getByRole('cell', { name: TEAMS_CONFIG.otherTeam, exact: true })).toHaveCount(0)
    await expect(page.getByText('集群合计和共享费用')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '创建团队' })).toHaveCount(0)
    for (const name of ['团队管理', '管理员账号', '审计日志'])
      await expect(page.getByRole('link', { name, exact: true })).toHaveCount(0)
  })
  test('lead is denied', async ({ page }) => {
    const headers = await csrfHeaders(page)
    for (const call of deniedCalls()) {
      const response = await page.request.fetch(call.url, { method: call.method, data: call.data, headers })
      expect(response.status(), `${call.method} ${call.url}`).toBe(403)
    }
  })
})
test('anonymous is denied', async () => {
  const context = await request.newContext()
  for (const call of deniedCalls()) {
    const response = await context.fetch(call.url, {
      method: call.method,
      data: call.data,
      headers: { 'Content-Type': 'application/json' },
      maxRedirects: 0,
    })
    expect(response.status(), `${call.method} ${call.url}`).toBe(401)
  }
  for (const path of ['me', 'jobs', 'history', 'team', 'files', 'dir']) {
    const response = await context.get(memberUrl(path), { maxRedirects: 0 })
    expect(response.status(), path).toBe(401)
  }
  await context.dispose()
})
test.describe('v2 member scope', () => {
  test.skip(!TEAMS_CONFIG.memberEmail || !TEAMS_CONFIG.memberTeam, 'E2E_MEMBER_* not set')
  test.setTimeout(JOB_TIMEOUT + 5 * 60 * 1000)
  test.beforeEach(async ({ page }) => {
    await loginAs(page, TEAMS_CONFIG.memberEmail, TEAMS_CONFIG.memberPassword)
  })
  test('member is denied outside own scope', async ({ page }) => {
    const headers = await csrfHeaders(page)
    const { root, otherRoot } = await teamRoot(page)
    const q = teamQuotaQuery()
    const base = `${baseUrl()}/pcui`
    const other = TEAMS_CONFIG.otherTeam
    const denied = [
      { method: 'GET', url: `${base}/team-quota/teams?${q}` },
      { method: 'GET', url: `${base}/team-quota/usage?${q}&start=2026-01-01&end=2026-02-01` },
      { method: 'GET', url: `${base}/team-quota/lifecycle/teams?${q}` },
      { method: 'POST', url: `${base}/team-quota/teams?${q}`, data: teamBody },
      { method: 'GET', url: `${base}/manager/queue_status?${q}` },
      { method: 'GET', url: `${base}/manager/list_users` },
      { method: 'GET', url: `${base}/team-quota/member/jobs?${q}&team=${other}` },
      { method: 'POST', url: `${base}/team-quota/member/jobs?${q}&team=${other}`, data: jobBody('#!/bin/bash\ntrue\n') },
      { method: 'GET', url: memberUrl('files', `&path=${encodeURIComponent(`${root}/../${other}/x`)}`) },
      { method: 'GET', url: memberUrl('dir', `&path=${encodeURIComponent(otherRoot)}`) },
    ]
    for (const call of denied) {
      const response = await page.request.fetch(call.url, { method: call.method, data: call.data, headers })
      expect(response.status(), `${call.method} ${call.url}`).toBe(403)
    }
  })
  test('a symlink into another team directory is rejected', async ({ page }) => {
    const headers = await csrfHeaders(page)
    const { root, otherRoot } = await teamRoot(page)
    const link = `${root}/.pcui-e2e-link-${Date.now()}`
    const submit = await page.request.post(memberUrl('jobs'), {
      headers, data: jobBody(`#!/bin/bash\nln -s ${otherRoot} ${link}\n`),
    })
    expect(submit.status()).toBe(201)
    const jobId = (await submit.json()).job_id
    await expect
      .poll(async () => {
        const response = await page.request.get(memberUrl(`jobs/${jobId}`))
        return response.ok() ? (await response.json()).state : 'UNKNOWN'
      }, { timeout: JOB_TIMEOUT, intervals: [10000] })
      .toMatch(/^(COMPLETED|FAILED)/)
    const listing = await page.request.get(memberUrl('dir', `&path=${encodeURIComponent(link)}`))
    expect(listing.status()).toBe(403)
  })
})
