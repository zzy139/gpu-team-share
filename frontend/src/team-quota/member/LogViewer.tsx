// Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
// with the License. A copy of the License is located at
//
// http://aws.amazon.com/apache2.0/
//
// or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
// OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
// limitations under the License.
import {
  Alert,
  Box,
  Button,
  Container,
  Header,
  SpaceBetween,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {errorMessage} from '../api'
import {decodeBase64Text, ReadMemberFile} from './api'
import {MemberTarget} from './types'

// The server returns at most 16 KiB per call so the answer fits in one SSM command output.
export const CHUNK_BYTES = 16384

interface Loaded {
  path: string
  size: number
  offset: number // offset of the first loaded byte
  text: string
}

/** Shows the end of a file (tail) and loads earlier chunks on demand. With ``follow`` (a running job) the tail is
 * read again whenever ``refreshKey`` changes and the view scrolls to the end, unless earlier content was loaded.
 * After ``follow`` turns false (the job ended) the next refresh still reads the tail, so the final output shows. */
export default function LogViewer({
  target,
  path,
  title,
  follow = false,
  refreshKey,
}: {
  target: MemberTarget | null
  path: string | null
  title: string
  follow?: boolean
  refreshKey?: number
}) {
  const {t} = useTranslation()
  const [loaded, setLoaded] = React.useState<Loaded | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(false)
  const earlierLoaded = React.useRef(false)
  const lastKey = React.useRef<number | undefined>(refreshKey)
  const followed = React.useRef(follow)
  const pre = React.useRef<HTMLPreElement>(null)

  const loadTail = React.useCallback(() => {
    if (!target || !path) return
    setLoading(true)
    setError(null)
    ReadMemberFile(target, path, 'tail', 0, CHUNK_BYTES)
      .then(chunk => {
        earlierLoaded.current = false
        setLoaded({
          path,
          size: chunk.size,
          offset: chunk.offset,
          text: decodeBase64Text(chunk.content_b64),
        })
      })
      .catch(err => setError(errorMessage(err)))
      .finally(() => setLoading(false))
  }, [target, path])

  React.useEffect(() => {
    setLoaded(null)
    loadTail()
  }, [loadTail])

  // follow a running job: read the tail again on every refresh of the job. Once the job was followed, the
  // refresh that reports its end (follow turns false) reads the tail once more, so the last lines show.
  React.useEffect(() => {
    if (follow) followed.current = true
    if (lastKey.current === refreshKey) return
    lastKey.current = refreshKey
    if (followed.current && !earlierLoaded.current) loadTail()
  }, [follow, refreshKey, loadTail])

  React.useEffect(() => {
    if (follow && !earlierLoaded.current && pre.current)
      pre.current.scrollTop = pre.current.scrollHeight
  }, [follow, loaded])

  const loadEarlier = () => {
    if (!target || !path || !loaded || loaded.offset <= 0) return
    const offset = Math.max(0, loaded.offset - CHUNK_BYTES)
    const limit = loaded.offset - offset
    setLoading(true)
    earlierLoaded.current = true
    ReadMemberFile(target, path, 'offset', offset, limit)
      .then(chunk =>
        setLoaded(prev =>
          prev
            ? {
                ...prev,
                offset: chunk.offset,
                text: decodeBase64Text(chunk.content_b64) + prev.text,
              }
            : prev,
        ),
      )
      .catch(err => setError(errorMessage(err)))
      .finally(() => setLoading(false))
  }

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={path || t('teamQuota.member.logs.noPath')}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                disabled={!loaded || loaded.offset <= 0}
                loading={loading}
                onClick={loadEarlier}
              >
                {t('teamQuota.member.logs.loadEarlier')}
              </Button>
              <Button iconName="refresh" disabled={!path} onClick={loadTail}>
                {t('teamQuota.member.refresh')}
              </Button>
            </SpaceBetween>
          }
        >
          {title}
        </Header>
      }
    >
      {error && <Alert type="error">{error}</Alert>}
      {loaded && (
        <SpaceBetween size="xs">
          <Box variant="small">
            {t('teamQuota.member.logs.range', {
              from: loaded.offset,
              to: loaded.size,
              size: loaded.size,
            })}
          </Box>
          {follow && (
            <Box variant="small">{t('teamQuota.member.logs.following')}</Box>
          )}
          <pre
            ref={pre}
            data-testid="log-content"
            style={{
              whiteSpace: 'pre-wrap',
              maxHeight: 480,
              overflow: 'auto',
              margin: 0,
            }}
          >
            {loaded.text}
          </pre>
        </SpaceBetween>
      )}
    </Container>
  )
}
