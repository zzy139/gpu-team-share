// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {ColumnLayout, FormField, Select} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {setState, useState} from '../../store'
import ClusterSelect, {useSelectedTeamTarget} from '../ClusterSelect'
import {useTeamsQuery} from '../queries'
import {MemberTarget} from '../member/types'

export const SELECTED_LEAD_TEAM_PATH = ['app', 'teamQuota', 'leadTeam']

/**
 * Cluster and team of the team-lead pages: the cluster from the cluster select, the team from the teams the caller
 * may see (a lead: own teams; an admin: every team).
 */
export function useLeadTarget(): {
  target: MemberTarget | null
  teams: string[]
  setTeam: (team: string) => void
  isLoading: boolean
} {
  const cluster = useSelectedTeamTarget()
  const {data, isLoading} = useTeamsQuery(cluster)
  const selected: string | undefined = useState(SELECTED_LEAD_TEAM_PATH)
  const teams = React.useMemo(
    () => (data?.teams || []).map(team => team.name),
    [data],
  )
  const team = selected && teams.includes(selected) ? selected : teams[0]
  const target = React.useMemo(
    () =>
      cluster && team
        ? {clusterName: cluster.clusterName, region: cluster.region, team}
        : null,
    [cluster, team],
  )
  const setTeam = React.useCallback(
    (value: string) => setState(SELECTED_LEAD_TEAM_PATH, value),
    [],
  )
  return {target, teams, setTeam, isLoading}
}

export function LeadTeamSelect() {
  const {t} = useTranslation()
  const {target, teams, setTeam, isLoading} = useLeadTarget()
  return (
    <ColumnLayout columns={2}>
      <ClusterSelect />
      <FormField label={t('teamQuota.member.teamLabel')}>
        <Select
          ariaLabel={t('teamQuota.member.teamLabel')}
          selectedOption={
            target?.team ? {label: target.team, value: target.team} : null
          }
          options={teams.map(team => ({label: team, value: team}))}
          statusType={isLoading ? 'loading' : 'finished'}
          onChange={({detail}) => setTeam(detail.selectedOption.value!)}
        />
      </FormField>
    </ColumnLayout>
  )
}
