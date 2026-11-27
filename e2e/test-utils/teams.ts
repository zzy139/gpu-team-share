// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import { Page } from "@playwright/test";
import { ENVIRONMENT_CONFIG } from "../configs/environment";

export const TEAMS_CONFIG = {
  adminEmail: process.env.E2E_ADMIN_EMAIL || '',
  adminPassword: process.env.E2E_ADMIN_PASSWORD || '',
  leadEmail: process.env.E2E_LEAD_EMAIL || '',
  leadPassword: process.env.E2E_LEAD_PASSWORD || '',
  cluster: process.env.E2E_CLUSTER || 'hpc-poc',
  region: process.env.E2E_REGION || 'us-west-2',
  teambLimit: process.env.E2E_TEAMB_LIMIT || '',
  lifecycleTeam: process.env.E2E_LIFECYCLE_TEAM || '',
  lifecycleMemberEmail: process.env.E2E_LIFECYCLE_MEMBER_EMAIL || '',
  lifecycleLead: process.env.E2E_LIFECYCLE_LEAD || '',
  memberEmail: process.env.E2E_MEMBER_EMAIL || '',
  memberPassword: process.env.E2E_MEMBER_PASSWORD || '',
  memberTeam: process.env.E2E_MEMBER_TEAM || '',
  otherTeam: process.env.E2E_OTHER_TEAM || 'teamb',
}

/** Base URL of the PCUI deployment without a trailing slash, e.g. https://xyz.execute-api.us-west-2.amazonaws.com */
export function baseUrl() {
  return ENVIRONMENT_CONFIG.URL.replace(/\/+$/, '').replace(/\/pcui$/, '')
}

export async function loginAs(page: Page, email: string, password: string) {
  await page.goto(ENVIRONMENT_CONFIG.URL);
  await page.getByRole('textbox', { name: 'name@host.com' }).fill(email);
  await page.getByRole('textbox', { name: 'Password' }).fill(password);
  await page.getByRole('button', { name: 'submit' }).click();
  await page.waitForURL(/\/pcui\//);
}

/** Read the CSRF token the same way the frontend does, for write calls made with page.request. */
export async function csrfHeaders(page: Page) {
  const response = await page.request.get(`${baseUrl()}/pcui/csrf`);
  const { csrf_token } = await response.json();
  return { 'X-CSRF-Token': csrf_token, 'Content-Type': 'application/json' };
}

export function teamQuotaQuery() {
  return `cluster_name=${encodeURIComponent(TEAMS_CONFIG.cluster)}&region=${encodeURIComponent(TEAMS_CONFIG.region)}`
}

/** True when the team lifecycle side stack is deployed (admin session required). */
export async function lifecycleConfigured(page: Page) {
  const response = await page.request.get(`${baseUrl()}/pcui/team-quota/lifecycle/teams?${teamQuotaQuery()}`)
  return response.ok() && (await response.json()).configured === true
}
