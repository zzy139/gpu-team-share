// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
//
// v2 business-user checks: submit by instance type, runtime environments, templates, resubmit, notifications.
// Env: E2E_TEST_URL, E2E_MEMBER_EMAIL/PASSWORD (member of E2E_MEMBER_TEAM), E2E_MEMBER_TEAM, E2E_OTHER_TEAM,
//      E2E_CLUSTER, E2E_REGION,
//      E2E_V2_TYPE (instance type to submit to, default g6e.xlarge),
//      E2E_V2_BUILD_ENVS=1 (optional: build a CUDA image env and a conda env and run a job in each; ~30 min).
import { expect, Page, request, test } from '@playwright/test';
import { baseUrl, csrfHeaders, loginAs, TEAMS_CONFIG, teamQuotaQuery } from '../test-utils/teams';
const TYPE = process.env.E2E_V2_TYPE || 'g6e.xlarge'
const BUILD_ENVS = process.env.E2E_V2_BUILD_ENVS === '1'
const JOB_TIMEOUT = 20 * 60 * 1000
const BUILD_TIMEOUT = 40 * 60 * 1000
const STAMP = Date.now().toString(36)
function memberUrl(path: string, extra = '') {
  return `${baseUrl()}/pcui/team-quota/member/${path}?${teamQuotaQuery()}&team=${TEAMS_CONFIG.memberTeam}${extra}`
}
async function jobState(page: Page, jobId: string) {
  const response = await page.request.get(memberUrl(`jobs/${jobId}`))
  return response.ok() ? (await response.json()).state : 'UNKNOWN'
}
async function waitForEnv(page: Page, name: string, version: string) {
  await expect
    .poll(async () => {
      const list = await (await page.request.get(memberUrl('envs'))).json()
      const env = list.envs.find((e: any) => e.name === name && e.version === version)
      return env ? env.status : 'missing'
    }, { timeout: BUILD_TIMEOUT, intervals: [20000] })
    .toMatch(/^(succeeded|failed)$/)
}
test.describe.configure({ mode: 'serial' })
// Skipped since v3: these UI checks use the v2 English labels and the removed publish flow; the v3-admin,
// v3-lead and v3-member specs replace them. The anonymous-denial API checks below still run.
test.describe.skip('v2 member', () => {
  test.skip(!TEAMS_CONFIG.memberEmail || !TEAMS_CONFIG.memberTeam, 'E2E_MEMBER_* not set')
  test.setTimeout(JOB_TIMEOUT + 5 * 60 * 1000)
  test.beforeEach(async ({ page }) => {
    await loginAs(page, TEAMS_CONFIG.memberEmail, TEAMS_CONFIG.memberPassword)
  })
  test('member pages include environments and templates, without admin pages', async ({ page }) => {
    for (const link of ['我的作业', '提交作业', 'Environments', 'Job templates', '我的团队'])
      await expect(page.getByRole('link', { name: link }).first()).toBeVisible()
    for (const link of ['Clusters', 'Team quota', 'Audit log'])
      await expect(page.getByRole('link', { name: link })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /^Notifications/ })).toBeVisible()
  })
  test('submit form shows limit, running, remaining, size per type and the whole-machine hint', async ({ page }) => {
    const options = await (await page.request.get(memberUrl('options'))).json()
    expect(options.types.length).toBeGreaterThan(0)
    for (const type of options.types) {
      expect(type.remaining).toBe(Math.max(type.limit - type.running, 0))
      expect(type.borrow_allowed).toBe(true)
    }
    await page.getByRole('link', { name: '提交作业' }).first().click()
    for (const header of ['Instance type', 'GPUs', 'vCPUs', 'Memory', 'Team limit', 'Running', 'Remaining'])
      await expect(page.getByRole('columnheader', { name: header }).first()).toBeVisible({ timeout: 90000 })
    await expect(page.getByText('Each job gets whole machines. Pick the smallest size that fits your job.')).toBeVisible()
    await expect(page.getByText(TYPE, { exact: true }).first()).toBeVisible()
    await expect(page.locator('body')).not.toContainText(/billing|points|计费/i)
  })
  test('submit by instance type, then resubmit and a template prefill the same job', async ({ page }) => {
    const headers = await csrfHeaders(page)
    const legacy = await page.request.post(memberUrl('jobs'), {
      headers, data: { partition: 'g6e-xlarge', gpus: 1, time_minutes: 5, script_content: '#!/bin/bash\ntrue\n' },
    })
    expect(legacy.status()).toBe(400)
    await page.getByRole('link', { name: '提交作业' }).first().click()
    await page.getByRole('row', { name: new RegExp(TYPE.replace('.', '\\.')) }).getByRole('radio').click()
    await page.getByLabel('作业名称（可选）').fill(`v2-${STAMP}`)
    await page.getByLabel('时长上限（分钟）').fill('10')
    await page.getByLabel('脚本内容').fill('#!/bin/bash\nnvidia-smi -L\nhostname\n')
    await page.getByRole('button', { name: '提交', exact: true }).click()
    await expect(page).toHaveURL(/\/pcui\/my-jobs\/\d+/, { timeout: 90000 })
    const jobId = page.url().split('/').pop()!
    const resubmit = await (await page.request.get(memberUrl(`jobs/${jobId}/resubmit`))).json()
    expect(resubmit.job.instance_type).toBe(TYPE)
    expect(resubmit.job.script_content).toContain('nvidia-smi -L')
    await page.getByRole('button', { name: /^Resubmit/ }).first().click()
    await expect(page).toHaveURL(new RegExp(`/pcui/submit\\?resubmit=${jobId}`))
    await expect(page.getByText(`Form filled from job ${jobId}.`)).toBeVisible({ timeout: 90000 })
    await page.getByRole('button', { name: 'Save as template' }).click()
    await page.getByLabel('Template name').fill(`v2-${STAMP}`)
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    const templates = await (await page.request.get(memberUrl('templates'))).json()
    const template = templates.templates.find((t: any) => t.name === `v2-${STAMP}`)
    expect(template.job.instance_type).toBe(TYPE)
    await page.getByRole('link', { name: 'Job templates' }).first().click()
    await expect(page.getByRole('cell', { name: `v2-${STAMP}` })).toBeVisible({ timeout: 90000 })
    const removed = await page.request.delete(memberUrl(`templates/${template.id}`), { headers })
    expect(removed.ok()).toBe(true)
    await page.request.post(memberUrl(`jobs/${jobId}/cancel`), { headers, data: {} })
  })
  test('environments page lists team and shared environments with the storage usage', async ({ page }) => {
    const list = await (await page.request.get(memberUrl('envs'))).json()
    expect(list.team).toBe(TEAMS_CONFIG.memberTeam)
    expect(list.roles.member).toBe(true)
    expect(list.limits.registries).toEqual(expect.arrayContaining(['docker.io', 'nvcr.io', 'public.ecr.aws', 'ghcr.io']))
    await page.getByRole('link', { name: 'Environments' }).first().click()
    await expect(page.getByText('Team environments')).toBeVisible({ timeout: 90000 })
    await expect(page.getByText('Shared environments')).toBeVisible()
    await expect(page.getByText('Environment storage')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Create environment' })).toBeVisible()
  })
  test('invalid environments are rejected before any build', async ({ page }) => {
    const headers = await csrfHeaders(page)
    const cases = [
      { data: { name: 'x', version: '1', source: 'image', image: 'evil.example.com/x:1' }, status: 400 },
      { data: { name: 'x', version: '1', source: 'image', image: 'nvcr.io/x;rm -rf /' }, status: 400 },
      { data: { name: 'X', version: '1', source: 'image', image: 'nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04' }, status: 400 },
      { data: { name: 'x', version: '1', source: 'dockerfile', dockerfile: 'FROM x\n' + '#'.repeat(16000) }, status: 413 },
    ]
    for (const c of cases) {
      const response = await page.request.post(memberUrl('envs'), { headers, data: c.data })
      expect(response.status(), JSON.stringify(c.data).slice(0, 80)).toBe(c.status)
    }
  })
  test('build a CUDA image and a conda env, then run a job in each', async ({ page }) => {
    test.skip(!BUILD_ENVS, 'E2E_V2_BUILD_ENVS not set')
    test.setTimeout(2 * BUILD_TIMEOUT + 2 * JOB_TIMEOUT)
    const headers = await csrfHeaders(page)
    const version = `e2e-${STAMP}`
    const created = [
      await page.request.post(memberUrl('envs'), { headers, data: {
        name: 'cuda', version, source: 'image', image: 'nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04' } }),
      await page.request.post(memberUrl('envs'), { headers, data: {
        name: 'py', version, source: 'conda', environment_yml: 'dependencies:\n  - python=3.11\n  - numpy\n' } }),
    ]
    for (const response of created) expect(response.status()).toBe(201)
    for (const name of ['cuda', 'py']) await waitForEnv(page, name, version)
    const list = await (await page.request.get(memberUrl('envs'))).json()
    for (const name of ['cuda', 'py']) {
      const env = list.envs.find((e: any) => e.name === name && e.version === version)
      expect(env.status, `${name} ${env.failure}`).toBe('succeeded')
      expect(env.size_bytes).toBeGreaterThan(0)
    }
    const options = await (await page.request.get(memberUrl('options'))).json()
    expect(options.envs.map((e: any) => `${e.name}/${e.version}`)).toEqual(
      expect.arrayContaining([`cuda/${version}`, `py/${version}`]))
    const scripts: Record<string, string> = {
      cuda: '#!/bin/bash\nnvidia-smi -L\n',
      py: '#!/bin/bash\npython -c "import numpy, sys; print(sys.version, numpy.__version__)"\n',
    }
    for (const name of ['cuda', 'py']) {
      const submit = await page.request.post(memberUrl('jobs'), { headers, data: {
        instance_type: TYPE, nodes: 1, time_minutes: 15, borrow: false,
        env: { scope: 'team', team: TEAMS_CONFIG.memberTeam, name, version }, script_content: scripts[name] } })
      expect(submit.status()).toBe(201)
      const jobId = (await submit.json()).job_id
      await expect.poll(() => jobState(page, jobId), { timeout: JOB_TIMEOUT, intervals: [15000] })
        .toMatch(/^(COMPLETED|FAILED|CANCELLED|TIMEOUT)/)
      expect(await jobState(page, jobId)).toMatch(/^COMPLETED/)
    }
  })
  test('notifications and preferences answer for the member', async ({ page }) => {
    const notifications = await page.request.get(memberUrl('notifications'))
    expect(notifications.status()).toBe(200)
    expect(typeof (await notifications.json()).unread).toBe('number')
    const prefs = await (await page.request.get(memberUrl('preferences'))).json()
    expect(prefs.email.address).toBe(TEAMS_CONFIG.memberEmail.toLowerCase())
    await page.getByRole('button', { name: 'Notification settings' }).click()
    await expect(page.getByText('Show job-end notifications in the portal')).toBeVisible()
  })
  test('member is denied on admin routes and other teams', async ({ page }) => {
    const headers = await csrfHeaders(page)
    const q = teamQuotaQuery()
    const base = `${baseUrl()}/pcui/team-quota`
    const other = TEAMS_CONFIG.otherTeam
    const denied = [
      { method: 'GET', url: `${base}/audit` },
      { method: 'GET', url: `${base}/lead/jobs?${q}&team=${TEAMS_CONFIG.memberTeam}` },
      { method: 'POST', url: `${base}/teams/${TEAMS_CONFIG.memberTeam}/members/x/offboard?${q}`, data: { jobs: 'finish' } },
      { method: 'PATCH', url: `${base}/teams/${TEAMS_CONFIG.memberTeam}?${q}`, data: { budget_usd: 1 } },
      { method: 'GET', url: `${base}/member/envs?${q}&team=${other}` },
      { method: 'POST', url: `${base}/member/envs?${q}`, data: { team: other, name: 'x', version: '1', source: 'image', image: 'nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04' } },
    ]
    for (const call of denied) {
      const response = await page.request.fetch(call.url, { method: call.method, data: call.data, headers })
      expect(response.status(), `${call.method} ${call.url}`).toBe(403)
    }
  })
})
test('anonymous is denied on the v2 member routes', async () => {
  const context = await request.newContext()
  for (const path of ['options', 'envs', 'templates', 'notifications', 'preferences', 'budget', 'jobs/1/resubmit']) {
    const response = await context.get(memberUrl(path), { maxRedirects: 0 })
    expect(response.status(), path).toBe(401)
  }
  const post = await context.post(memberUrl('envs'), { data: {}, maxRedirects: 0 })
  expect(post.status()).toBe(401)
  await context.dispose()
})
