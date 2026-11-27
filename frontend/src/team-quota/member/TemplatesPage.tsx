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
  FormField,
  Header,
  Input,
  Modal,
  SpaceBetween,
  Table,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {useNavigate} from 'react-router-dom'
import {notify} from '../../model'
import {errorMessage} from '../api'
import MemberLayout from './MemberLayout'
import {
  useDeleteTemplateMutation,
  useMemberTarget,
  useTemplatesQuery,
  useUpdateTemplateMutation,
} from './queries'
import {JobTemplate} from './types'

const TEMPLATE_NAME_RE = /^[^\u0000-\u001f\u007f]{1,64}$/

/** The member's saved submit forms (the 我的模板 tab of 提交作业). ``onUse`` fills the submit form. */
export function TemplatesContent({
  onUse,
}: {
  onUse: (template: JobTemplate) => void
}) {
  const {t} = useTranslation()
  const {target} = useMemberTarget()
  const {data, isLoading, isError, error} = useTemplatesQuery(target)
  const update = useUpdateTemplateMutation(target)
  const remove = useDeleteTemplateMutation(target)
  const [renaming, setRenaming] = React.useState<JobTemplate | null>(null)
  const [name, setName] = React.useState('')
  const [nameError, setNameError] = React.useState<string | null>(null)
  const [toDelete, setToDelete] = React.useState<JobTemplate | null>(null)
  const rename = () => {
    if (!renaming) return
    if (!TEMPLATE_NAME_RE.test(name.trim())) {
      setNameError(t('teamQuota.member.templates.errors.name'))
      return
    }
    update.mutate(
      {id: renaming.id, body: {name: name.trim()}},
      {
        onSuccess: () => setRenaming(null),
        onError: err => notify(errorMessage(err), 'error'),
      },
    )
  }
  const confirmDelete = () => {
    const template = toDelete
    setToDelete(null)
    if (!template) return
    remove.mutate(template.id, {
      onSuccess: () =>
        notify(
          t('teamQuota.member.templates.deleted', {name: template.name}),
          'success',
        ),
      onError: err => notify(errorMessage(err), 'error'),
    })
  }
  return (
    <SpaceBetween size="l">
      {isError && <Alert type="error">{errorMessage(error)}</Alert>}
      <Table<JobTemplate>
        variant="container"
        trackBy="id"
        loading={!!target && isLoading}
        loadingText={t('teamQuota.member.loading')}
        items={data?.templates || []}
        empty={
          <Box textAlign="center">{t('teamQuota.member.templates.empty')}</Box>
        }
        header={
          <Header
            counter={`(${data?.templates.length ?? 0}/${data?.max ?? 50})`}
            description={t('teamQuota.member.templates.description')}
          >
            {t('teamQuota.member.templates.title')}
          </Header>
        }
        columnDefinitions={[
          {
            id: 'name',
            header: t('teamQuota.member.templates.name'),
            cell: item => item.name,
          },
          {
            id: 'type',
            header: t('teamQuota.member.submit.instanceType'),
            cell: item => item.job.instance_type || '-',
          },
          {
            id: 'nodes',
            header: t('teamQuota.member.submit.nodes'),
            cell: item => item.job.nodes ?? 1,
          },
          {
            id: 'env',
            header: t('teamQuota.member.submit.env'),
            cell: item =>
              item.job.env
                ? `${item.job.env.name}:${item.job.env.version}`
                : '-',
          },
          {
            id: 'updated',
            header: t('teamQuota.member.templates.updated'),
            cell: item => item.updated_at || '-',
          },
          {
            id: 'actions',
            header: '',
            cell: item => (
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => onUse(item)}>
                  {t('teamQuota.member.templates.use')}
                </Button>
                <Button
                  variant="link"
                  onClick={() => {
                    setName(item.name)
                    setNameError(null)
                    setRenaming(item)
                  }}
                >
                  {t('teamQuota.member.templates.rename')}
                </Button>
                <Button
                  variant="link"
                  ariaLabel={t('teamQuota.member.templates.deleteAria', {
                    name: item.name,
                  })}
                  onClick={() => setToDelete(item)}
                >
                  {t('teamQuota.actions.delete')}
                </Button>
              </SpaceBetween>
            ),
          },
        ]}
      />
      <Modal
        visible={!!renaming}
        onDismiss={() => setRenaming(null)}
        header={t('teamQuota.member.templates.rename')}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setRenaming(null)}>
                {t('teamQuota.actions.cancel')}
              </Button>
              <Button
                variant="primary"
                loading={update.isLoading}
                onClick={rename}
              >
                {t('teamQuota.actions.save')}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField
          label={t('teamQuota.member.templates.name')}
          errorText={nameError}
        >
          <Input
            ariaLabel={t('teamQuota.member.templates.name')}
            value={name}
            onChange={({detail}) => setName(detail.value)}
          />
        </FormField>
      </Modal>
      <Modal
        visible={!!toDelete}
        onDismiss={() => setToDelete(null)}
        header={t('teamQuota.member.templates.deleteTitle')}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setToDelete(null)}>
                {t('teamQuota.actions.cancel')}
              </Button>
              <Button variant="primary" onClick={confirmDelete}>
                {t('teamQuota.actions.delete')}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {t('teamQuota.member.templates.deleteBody', {name: toDelete?.name})}
      </Modal>
    </SpaceBetween>
  )
}

/** Stand-alone page (the /templates route redirects to 提交作业 → 我的模板). */
export default function TemplatesPage() {
  const navigate = useNavigate()
  return (
    <MemberLayout page="templates">
      <TemplatesContent
        onUse={item =>
          navigate(`/submit?template=${encodeURIComponent(item.id)}`)
        }
      />
    </MemberLayout>
  )
}
