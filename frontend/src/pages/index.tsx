// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.

import React from 'react'
import {BrowserRouter, Route, Routes, Navigate} from 'react-router-dom'

import {LoadInitialState} from '../model'

import Clusters from '../old-pages/Clusters/Clusters'
import Configure from '../old-pages/Configure/Configure'
import Users from '../old-pages/Users/Users'

// Components
import {NoMatch} from '../components/NoMatch'
import {Images} from '../old-pages/Images'
import {Logs} from '../old-pages/Logs'
import {useLoadingState} from '../components/useLoadingState'
import {useState} from '../store'
import AuditPage from '../team-quota/AuditPage'
import CapacityPage from '../team-quota/CapacityPage'
import EnvsPage, {LeadEnvsPage} from '../team-quota/member/EnvsPage'
import JobDetailPage from '../team-quota/member/JobDetailPage'
import MyJobsPage from '../team-quota/member/MyJobsPage'
import MyTeamPage from '../team-quota/member/MyTeamPage'
import SubmitJobPage from '../team-quota/member/SubmitJobPage'
import {
  hasMemberRole,
  isAdmin,
  isMemberOnly,
  isTeamLeadOnly,
} from '../team-quota/roles'
import {TeamDetailPage, TeamTabRedirect} from '../team-quota/TeamDetail'
import TeamsPage from '../team-quota/TeamsPage'
import TeamUsagePage from '../team-quota/TeamUsagePage'

// 我的团队 adapts to the role (lead/admin: the team page, member: 概览 and 费用); every router has it once.
function myTeamRoutes() {
  return [
    <Route key="my-team" path="my-team" element={<MyTeamPage />} />,
    <Route key="my-team-tab" path="my-team/:tab" element={<MyTeamPage />} />,
  ]
}
// Self-service pages for business users (`member-<team>` groups). They only call /team-quota/member/*.
function memberRoutes() {
  return [
    <Route key="my-jobs" path="my-jobs" element={<MyJobsPage />} />,
    <Route key="my-job" path="my-jobs/:jobId" element={<JobDetailPage />} />,
    <Route key="submit" path="submit" element={<SubmitJobPage />} />,
    <Route key="envs" path="envs" element={<EnvsPage />} />,
    // Job templates are part of 提交作业.
    <Route
      key="templates"
      path="templates"
      element={<Navigate replace to="/submit?tab=templates" />}
    />,
  ]
}
// Team pages of leads and admins. The old team-jobs and quota-holders pages are tabs of the team page now;
// members see the environments through the member routes instead.
function leadRoutes(member: boolean) {
  return [
    <Route key="teams" path="teams" element={<TeamsPage />} />,
    <Route key="team" path="teams/:team" element={<TeamDetailPage />} />,
    <Route
      key="team-tab"
      path="teams/:team/:tab"
      element={<TeamDetailPage />}
    />,
    <Route key="team-usage" path="team-usage" element={<TeamUsagePage />} />,
    <Route
      key="team-jobs"
      path="team-jobs"
      element={<TeamTabRedirect tab="jobs" />}
    />,
    <Route
      key="quota-holders"
      path="quota-holders"
      element={<TeamTabRedirect tab="members" />}
    />,
    ...(member
      ? []
      : [<Route key="envs" path="envs" element={<LeadEnvsPage />} />]),
  ]
}

function MemberRouter() {
  return (
    <BrowserRouter basename="/pcui">
      <Routes>
        <Route path="index.html" element={<Navigate replace to="/my-jobs" />} />
        <Route index element={<Navigate replace to="/my-jobs" />} />
        {memberRoutes()}
        {myTeamRoutes()}
        <Route path="*" element={<NoMatch />} />
      </Routes>
    </BrowserRouter>
  )
}

// Team leads (team-<team> groups without admin) only get the team pages:
// every other page calls admin-only endpoints.
function TeamLeadRouter({member}: {member: boolean}) {
  return (
    <BrowserRouter basename="/pcui">
      <Routes>
        <Route path="index.html" element={<Navigate replace to="/my-team" />} />
        <Route index element={<Navigate replace to="/my-team" />} />
        {leadRoutes(member)}
        {myTeamRoutes()}
        {member && memberRoutes()}
        <Route path="*" element={<NoMatch />} />
      </Routes>
    </BrowserRouter>
  )
}

export default function App() {
  const identity = useState(['identity'])
  const teamLeadOnly = isTeamLeadOnly(identity)
  const memberOnly = isMemberOnly(identity)
  const member = hasMemberRole(identity)
  const admin = isAdmin(identity)
  const adminRouter = (
    <BrowserRouter basename="/pcui">
      <Routes>
        <Route
          path="index.html"
          element={<Navigate replace to="/clusters" />}
        />
        <Route index element={<Navigate replace to="/clusters" />} />
        <Route path="clusters" element={<Clusters />}>
          <Route path=":clusterName" element={<div></div>}>
            <Route path=":tab" element={<div></div>} />
          </Route>
        </Route>
        <Route path="clusters/:clusterName/logs" element={<Logs />} />
        <Route path="configure" element={<Configure />} />
        <Route path="images" element={<Images />} />
        <Route path="users" element={<Users />} />
        <Route path="capacity" element={<CapacityPage />} />
        <Route path="audit" element={<AuditPage />} />
        {leadRoutes(member)}
        {myTeamRoutes()}
        {member && memberRoutes()}
        <Route path="*" element={<NoMatch />} />
      </Routes>
    </BrowserRouter>
  )
  const {loading, content} = useLoadingState(
    memberOnly ? (
      <MemberRouter />
    ) : teamLeadOnly ? (
      <TeamLeadRouter member={member} />
    ) : (
      adminRouter
    ),
  )

  React.useEffect(() => {
    // Only administrators may call the upstream endpoints that LoadInitialState uses.
    if (!loading && admin) {
      LoadInitialState()
    }
  }, [loading, admin])

  return content
}
