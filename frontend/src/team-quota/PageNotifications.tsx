// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {Flashbar, SpaceBetween} from '@cloudscape-design/components'
import React from 'react'
import {useState} from '../store'

/**
 * Content of the Layout `notifications` slot: the app flash messages (what Layout shows by default) followed by
 * the page alerts. Keeping alerts out of the content lets a full-page table be the only content, as on Clusters.
 */
export default function PageNotifications({
  children,
}: {
  children?: React.ReactNode
}) {
  const messages = useState(['app', 'messages']) || []
  const alerts = React.Children.toArray(children).filter(Boolean)
  return (
    <SpaceBetween size="xs">
      <Flashbar items={messages} />
      {alerts}
    </SpaceBetween>
  )
}
