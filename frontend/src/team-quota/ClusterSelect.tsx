// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {FormField, Select, SelectProps} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {setState, useState} from '../store'
import {useTeamClustersQuery} from './queries'
import {TeamCluster, TeamTarget} from './types'

export const SELECTED_CLUSTER_PATH = ['app', 'teamQuota', 'cluster']

export function useSelectedTeamTarget(): TeamTarget | null {
  const selected: TeamCluster | undefined = useState(SELECTED_CLUSTER_PATH)
  return React.useMemo(
    () =>
      selected ? {clusterName: selected.name, region: selected.region} : null,
    [selected],
  )
}

export default function ClusterSelect() {
  const {t} = useTranslation()
  const region: string | undefined = useState(['app', 'selectedRegion'])
  const selected: TeamCluster | undefined = useState(SELECTED_CLUSTER_PATH)
  const {data, isLoading, isError} = useTeamClustersQuery(region)
  const clusters = React.useMemo(() => data?.clusters || [], [data])

  React.useEffect(() => {
    if (!clusters.length) return
    const stillListed = clusters.some(
      c => c.name === selected?.name && c.region === selected?.region,
    )
    if (!stillListed) setState(SELECTED_CLUSTER_PATH, clusters[0])
  }, [clusters, selected])

  const options: SelectProps.Option[] = clusters.map(c => ({
    label: c.name,
    value: `${c.region}/${c.name}`,
    description: c.region,
  }))
  const selectedOption =
    options.find(o => o.value === `${selected?.region}/${selected?.name}`) ||
    null

  return (
    <FormField label={t('teamQuota.cluster.label')}>
      <Select
        selectedOption={selectedOption}
        options={options}
        statusType={isLoading ? 'loading' : isError ? 'error' : 'finished'}
        loadingText={t('teamQuota.cluster.loading')}
        errorText={t('teamQuota.cluster.error')}
        empty={t('teamQuota.cluster.empty')}
        placeholder={t('teamQuota.cluster.placeholder')}
        onChange={({detail}) => {
          const cluster = clusters.find(
            c => `${c.region}/${c.name}` === detail.selectedOption.value,
          )
          if (cluster) setState(SELECTED_CLUSTER_PATH, cluster)
        }}
      />
    </FormField>
  )
}
