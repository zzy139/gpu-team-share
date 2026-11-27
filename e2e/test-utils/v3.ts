// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
//
// Shared helpers of the v3 specs. Credentials come only from environment variables (see test-utils/teams.ts) and
// are never logged. Screenshots go to SHOTS_DIR when it is set.
import { expect, Page } from '@playwright/test';
import { mkdirSync } from 'fs';
import { baseUrl, teamQuotaQuery } from './teams';

export const SHOTS_DIR = process.env.SHOTS_DIR || ''
let created = false

/** Full-page screenshot `<SHOTS_DIR>/<prefix>-<name>.png` (nothing when SHOTS_DIR is not set). */
export async function shot(page: Page, prefix: string, name: string) {
  if (!SHOTS_DIR) return
  if (!created) {
    mkdirSync(SHOTS_DIR, { recursive: true })
    created = true
  }
  await page.screenshot({ path: `${SHOTS_DIR}/${prefix}-${name}.png`, fullPage: true })
}

/** URL of a team-quota API route with the cluster query and optional extra query text. */
export function api(path: string, extra = '') {
  return `${baseUrl()}/pcui/team-quota/${path}?${teamQuotaQuery()}${extra}`
}

/** Open a page from the side navigation by its (Chinese) link text. */
export async function openMenu(page: Page, name: string) {
  await page.getByRole('link', { name, exact: true }).first().click()
  await page.waitForLoadState('networkidle')
}

/** The navigation links shown to the signed-in user, in order. */
export async function menuLinks(page: Page) {
  // Cloudscape renders a hidden (aria-hidden) mobile copy of the navigation first.
  const nav = page.locator('nav:not([aria-hidden="true"])').first()
  await expect(nav).toBeVisible()
  return (await nav.getByRole('link').allInnerTexts()).map(text => text.trim()).filter(Boolean)
}

/** Text of "最后更新：hh:mm:ss" on the page. */
export async function lastUpdated(page: Page) {
  return (await page.getByText(/^最后更新：\d\d:\d\d:\d\d$/).first().innerText()).trim()
}
