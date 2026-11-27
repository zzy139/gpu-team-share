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
  ColumnLayout,
  Container,
  FormField,
  Header,
  Input,
  Modal,
  Popover,
  ProgressBar,
  RadioGroup,
  Select,
  SpaceBetween,
  StatusIndicator,
  StatusIndicatorProps,
  Table,
  Tabs,
  Textarea,
  TextFilter,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {notify} from '../../model'
import {errorMessage} from '../api'
import {formatBytes} from '../format'
import LeadLayout from '../lead/LeadLayout'
import {useLeadTarget} from '../lead/LeadTeam'
import {utf8Length} from './api'
import LogViewer from './LogViewer'
import MemberLayout, {MemberTeamSelect} from './MemberLayout'
import {
  useCreateEnvMutation,
  useDeleteEnvMutation,
  useEnvsQuery,
  useEnvVisibilityMutation,
  useMemberTarget,
  useRetryEnvMutation,
} from './queries'
import {
  EnvCreateRequest,
  EnvEntry,
  EnvListResponse,
  EnvSource,
  EnvStatus,
  EnvVisibility,
  MemberTarget,
} from './types'

// Mirrors api/teams/validation.py.
export const ENV_NAME_RE = /^[a-z][a-z0-9-]{0,31}$/
export const ENV_VERSION_RE = /^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$/
const PYTHON_RE = /^3\.\d{1,2}$/
const INFO_MAX = 256
const IMAGE_MAX = 256

const STATUS: Record<EnvStatus, StatusIndicatorProps.Type> = {
  queued: 'pending',
  building: 'in-progress',
  succeeded: 'success',
  failed: 'error',
  unknown: 'warning',
}

export function EnvStatusBadge({env}: {env: EnvEntry}) {
  const {t} = useTranslation()
  return (
    <StatusIndicator type={STATUS[env.status] || 'warning'}>
      {t(`teamQuota.envs.status.${env.status}`)}
      {env.status === 'building' && env.progress ? ` (${env.progress})` : ''}
    </StatusIndicator>
  )
}

/** 仅本团队 / 所有人, with the warning that 所有人 makes the content readable for every user. */
export function VisibilityField({
  value,
  onChange,
  description,
}: {
  value: EnvVisibility
  onChange: (value: EnvVisibility) => void
  description?: string
}) {
  const {t} = useTranslation()
  return (
    <FormField
      label={t('teamQuota.envs.visibility.label')}
      description={description}
    >
      <SpaceBetween size="xs">
        <RadioGroup
          value={value}
          onChange={({detail}) => onChange(detail.value as EnvVisibility)}
          items={[
            {
              value: 'team',
              label: t('teamQuota.envs.visibility.team'),
              description: t('teamQuota.envs.visibility.teamDescription'),
            },
            {
              value: 'all',
              label: t('teamQuota.envs.visibility.all'),
              description: t('teamQuota.envs.visibility.allDescription'),
            },
          ]}
        />
        {value === 'all' && (
          <Alert type="warning">{t('teamQuota.envs.visibility.warning')}</Alert>
        )}
      </SpaceBetween>
    </FormField>
  )
}

/** An action the caller may not take: greyed out; clicking it shows why. */
function DisabledAction({label, reason}: {label: string; reason?: string}) {
  const {t} = useTranslation()
  return (
    <Popover
      size="small"
      position="top"
      triggerType="text"
      dismissButton={false}
      content={reason || t('teamQuota.envs.notAllowed')}
    >
      <Box variant="span" color="text-status-inactive">
        {label}
      </Box>
    </Popover>
  )
}

type CondaMode = 'environment_yml' | 'requirements_txt'
type Errors = Record<string, string | null>

/** Validated create body (errors are returned per field). */
export function buildEnvRequest(
  values: {
    source: EnvSource
    name: string
    version: string
    image: string
    dockerfile: string
    condaMode: CondaMode
    condaText: string
    python: string
    description: string
    software: string
  },
  limits: EnvListResponse['limits'] | undefined,
  t: (key: string, options?: any) => string,
): {body: EnvCreateRequest | null; errors: Errors} {
  const specMax = limits?.spec_max_bytes ?? 65536
  const dockerMax = limits?.dockerfile_max_bytes ?? 15000
  const oneLine = (value: string) =>
    value.length > INFO_MAX || /[\u0000-\u001f\u007f]/.test(value)
      ? t('teamQuota.envs.errors.info', {max: INFO_MAX})
      : null
  const text = (value: string, max: number) =>
    !value.trim()
      ? t('teamQuota.envs.errors.empty')
      : utf8Length(value) > max
      ? t('teamQuota.envs.errors.tooLarge', {max})
      : value.includes('\u0000')
      ? t('teamQuota.envs.errors.nul')
      : null
  const errors: Errors = {
    name: ENV_NAME_RE.test(values.name)
      ? null
      : t('teamQuota.envs.errors.name'),
    version: ENV_VERSION_RE.test(values.version)
      ? null
      : t('teamQuota.envs.errors.version'),
    description: oneLine(values.description),
    software: oneLine(values.software),
    image:
      values.source === 'image' &&
      (!values.image.trim() ||
        values.image.length > IMAGE_MAX ||
        /\s/.test(values.image))
        ? t('teamQuota.envs.errors.image', {max: IMAGE_MAX})
        : null,
    dockerfile:
      values.source === 'dockerfile'
        ? text(values.dockerfile, dockerMax)
        : null,
    condaText:
      values.source === 'conda' ? text(values.condaText, specMax) : null,
    python:
      values.source === 'conda' &&
      values.condaMode === 'requirements_txt' &&
      values.python &&
      !PYTHON_RE.test(values.python)
        ? t('teamQuota.envs.errors.python')
        : null,
  }
  if (Object.values(errors).some(Boolean)) return {body: null, errors}
  const body: EnvCreateRequest = {
    name: values.name,
    version: values.version,
    source: values.source,
  }
  if (values.source === 'image') body.image = values.image.trim()
  if (values.source === 'dockerfile') body.dockerfile = values.dockerfile
  if (values.source === 'conda') {
    body[values.condaMode] = values.condaText
    if (values.condaMode === 'requirements_txt' && values.python)
      body.python = values.python
  }
  if (values.description) body.description = values.description
  if (values.software) body.software = values.software
  return {body, errors}
}

function CreateEnvModal({
  target,
  limits,
  visible,
  onDismiss,
}: {
  target: MemberTarget | null
  limits: EnvListResponse['limits'] | undefined
  visible: boolean
  onDismiss: () => void
}) {
  const {t} = useTranslation()
  const mutation = useCreateEnvMutation(target)
  const [source, setSource] = React.useState<EnvSource>('image')
  const [failure, setFailure] = React.useState<string | null>(null)
  const [name, setName] = React.useState('')
  const [version, setVersion] = React.useState('')
  const [image, setImage] = React.useState('')
  const [dockerfile, setDockerfile] = React.useState('')
  const [condaMode, setCondaMode] = React.useState<CondaMode>('environment_yml')
  const [condaText, setCondaText] = React.useState('')
  const [python, setPython] = React.useState('')
  const [description, setDescription] = React.useState('')
  const [software, setSoftware] = React.useState('')
  const [visibility, setVisibility] = React.useState<EnvVisibility>('team')
  const [errors, setErrors] = React.useState<Errors>({})
  React.useEffect(() => {
    if (!visible) return
    setVisibility('team')
    setSource('image')
    setName('')
    setVersion('')
    setImage('')
    setDockerfile('')
    setCondaMode('environment_yml')
    setCondaText('')
    setPython('')
    setDescription('')
    setSoftware('')
    setErrors({})
  }, [visible])
  const submit = () => {
    const {body, errors: newErrors} = buildEnvRequest(
      {
        source,
        name,
        version,
        image,
        dockerfile,
        condaMode,
        condaText,
        python,
        description,
        software,
      },
      limits,
      t,
    )
    setErrors(newErrors)
    if (!body) return
    mutation.mutate(
      {...body, team: target?.team, visibility},
      {
        onSuccess: result => {
          notify(
            t('teamQuota.envs.created', {id: `${name}:${version}`}),
            'success',
          )
          if (result.warning) notify(result.warning, 'warning')
          onDismiss()
        },
        onError: err => setFailure(errorMessage(err)),
      },
    )
  }
  const imageTab = (
    <FormField
      label={t('teamQuota.envs.fields.image')}
      description={t('teamQuota.envs.fields.imageDescription', {
        registries: (limits?.registries || []).join(', '),
        repository: limits?.team_repository || '-',
      })}
      errorText={errors.image}
    >
      <Input
        ariaLabel={t('teamQuota.envs.fields.image')}
        placeholder="nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04"
        value={image}
        onChange={({detail}) => setImage(detail.value)}
      />
    </FormField>
  )
  const dockerTab = (
    <FormField
      label={t('teamQuota.envs.fields.dockerfile')}
      description={t('teamQuota.envs.fields.dockerfileDescription', {
        max: limits?.dockerfile_max_bytes ?? 15000,
      })}
      constraintText={t('teamQuota.member.submit.bytes', {
        bytes: utf8Length(dockerfile),
        max: limits?.dockerfile_max_bytes ?? 15000,
      })}
      errorText={errors.dockerfile}
      stretch
    >
      <Textarea
        ariaLabel={t('teamQuota.envs.fields.dockerfile')}
        rows={12}
        placeholder={'FROM public.ecr.aws/docker/library/ubuntu:22.04\n'}
        value={dockerfile}
        onChange={({detail}) => setDockerfile(detail.value)}
      />
    </FormField>
  )
  const condaTab = (
    <SpaceBetween size="m">
      <RadioGroup
        value={condaMode}
        onChange={({detail}) => setCondaMode(detail.value as CondaMode)}
        items={[
          {
            value: 'environment_yml',
            label: t('teamQuota.envs.fields.environmentYml'),
          },
          {
            value: 'requirements_txt',
            label: t('teamQuota.envs.fields.requirementsTxt'),
          },
        ]}
      />
      <FormField
        label={t(
          condaMode === 'environment_yml'
            ? 'teamQuota.envs.fields.environmentYml'
            : 'teamQuota.envs.fields.requirementsTxt',
        )}
        errorText={errors.condaText}
        stretch
      >
        <Textarea
          ariaLabel={t('teamQuota.envs.fields.condaSpec')}
          rows={12}
          placeholder={
            condaMode === 'environment_yml'
              ? 'dependencies:\n  - python=3.11\n  - numpy\n'
              : 'numpy\n'
          }
          value={condaText}
          onChange={({detail}) => setCondaText(detail.value)}
        />
      </FormField>
      {condaMode === 'requirements_txt' && (
        <FormField
          label={t('teamQuota.envs.fields.python')}
          errorText={errors.python}
        >
          <Input
            ariaLabel={t('teamQuota.envs.fields.python')}
            placeholder="3.11"
            value={python}
            onChange={({detail}) => setPython(detail.value)}
          />
        </FormField>
      )}
    </SpaceBetween>
  )
  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      size="large"
      header={t('teamQuota.envs.create.title')}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>{t('teamQuota.actions.cancel')}</Button>
            <Button
              variant="primary"
              loading={mutation.isLoading}
              onClick={submit}
            >
              {t('teamQuota.envs.create.submit')}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        <ColumnLayout columns={2}>
          {failure && (
            <Alert type="error" dismissible onDismiss={() => setFailure(null)}>
              {failure}
            </Alert>
          )}
          <FormField
            label={t('teamQuota.envs.fields.name')}
            description={t('teamQuota.envs.fields.nameDescription')}
            errorText={errors.name}
          >
            <Input
              ariaLabel={t('teamQuota.envs.fields.name')}
              value={name}
              onChange={({detail}) => setName(detail.value)}
            />
          </FormField>
          <FormField
            label={t('teamQuota.envs.fields.version')}
            description={t('teamQuota.envs.fields.versionDescription')}
            errorText={errors.version}
          >
            <Input
              ariaLabel={t('teamQuota.envs.fields.version')}
              value={version}
              onChange={({detail}) => setVersion(detail.value)}
            />
          </FormField>
          <FormField
            label={t('teamQuota.envs.fields.description')}
            errorText={errors.description}
          >
            <Input
              ariaLabel={t('teamQuota.envs.fields.description')}
              value={description}
              onChange={({detail}) => setDescription(detail.value)}
            />
          </FormField>
          <FormField
            label={t('teamQuota.envs.fields.software')}
            description={t('teamQuota.envs.fields.softwareDescription')}
            errorText={errors.software}
          >
            <Input
              ariaLabel={t('teamQuota.envs.fields.software')}
              value={software}
              onChange={({detail}) => setSoftware(detail.value)}
            />
          </FormField>
        </ColumnLayout>
        <VisibilityField
          value={visibility}
          onChange={setVisibility}
          description={t('teamQuota.envs.visibility.createDescription')}
        />
        <Tabs
          activeTabId={source}
          onChange={({detail}) => setSource(detail.activeTabId as EnvSource)}
          tabs={[
            {
              id: 'image',
              label: t('teamQuota.envs.sources.image'),
              content: imageTab,
            },
            {
              id: 'dockerfile',
              label: t('teamQuota.envs.sources.dockerfile'),
              content: dockerTab,
            },
            {
              id: 'conda',
              label: t('teamQuota.envs.sources.conda'),
              content: condaTab,
            },
          ]}
        />
      </SpaceBetween>
    </Modal>
  )
}

/** Change who can use every version of an environment name. */
function VisibilityModal({
  target,
  env,
  onDismiss,
}: {
  target: MemberTarget | null
  env: EnvEntry | null
  onDismiss: () => void
}) {
  const {t} = useTranslation()
  const mutation = useEnvVisibilityMutation(target)
  const [value, setValue] = React.useState<EnvVisibility>('team')
  const [failure, setFailure] = React.useState<string | null>(null)
  React.useEffect(() => {
    if (!env) return
    setValue(env.visibility)
    setFailure(null)
  }, [env])
  const save = () => {
    if (!env) return
    mutation.mutate(
      {env: {team: env.team, name: env.name}, visibility: value},
      {
        onSuccess: () => {
          notify(
            t('teamQuota.envs.visibility.changed', {
              name: env.name,
              visibility: t(`teamQuota.envs.visibility.${value}`),
            }),
            'success',
          )
          onDismiss()
        },
        onError: err => setFailure(errorMessage(err)),
      },
    )
  }
  return (
    <Modal
      visible={!!env}
      onDismiss={onDismiss}
      header={t('teamQuota.envs.visibility.changeTitle')}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>{t('teamQuota.actions.cancel')}</Button>
            <Button
              variant="primary"
              loading={mutation.isLoading}
              disabled={!env || value === env.visibility}
              onClick={save}
            >
              {t('teamQuota.actions.save')}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        {failure && <Alert type="error">{failure}</Alert>}
        <Box>
          {t('teamQuota.envs.visibility.changeBody', {
            name: env?.name,
            team: env?.team,
          })}
        </Box>
        <VisibilityField value={value} onChange={setValue} />
      </SpaceBetween>
    </Modal>
  )
}

function matches(env: EnvEntry, text: string) {
  const needle = text.trim().toLowerCase()
  if (!needle) return true
  return [
    env.name,
    env.version,
    env.team,
    env.owner,
    env.description,
    env.software,
    env.image,
  ].some(value => (value || '').toLowerCase().includes(needle))
}

/** One list: the team's environments and the 所有人 environments of other teams, with a team filter and search. */
export function EnvsContent({target}: {target: MemberTarget | null}) {
  const {t} = useTranslation()
  const {data, isLoading, isError, error, refetch, isFetching} =
    useEnvsQuery(target)
  const retry = useRetryEnvMutation(target)
  const remove = useDeleteEnvMutation(target)
  const [creating, setCreating] = React.useState(false)
  const [toDelete, setToDelete] = React.useState<EnvEntry | null>(null)
  const [toChange, setToChange] = React.useState<EnvEntry | null>(null)
  const [logOf, setLogOf] = React.useState<EnvEntry | null>(null)
  const [teamFilter, setTeamFilter] = React.useState('')
  const [filterText, setFilterText] = React.useState('')
  const member = !!data?.roles.member
  const usage = data?.usage
  const ownTeam = data?.team || target?.team
  const items = React.useMemo(
    () =>
      (data?.envs || []).filter(
        env =>
          (!teamFilter || env.team === teamFilter) && matches(env, filterText),
      ),
    [data, teamFilter, filterText],
  )
  const teamOptions = [
    {value: '', label: t('teamQuota.envs.filter.allTeams')},
    ...(data?.teams || []).map(team => ({
      value: team,
      label:
        team === ownTeam ? t('teamQuota.envs.filter.ownTeam', {team}) : team,
    })),
  ]
  const act = (
    mutation: typeof retry,
    env: EnvEntry,
    messageKey: 'teamQuota.envs.retried' | 'teamQuota.envs.deleted',
  ) =>
    mutation.mutate(env, {
      onSuccess: () =>
        notify(t(messageKey, {id: `${env.name}:${env.version}`}), 'success'),
      onError: err => notify(errorMessage(err), 'error'),
    })
  const confirmDelete = () => {
    const env = toDelete
    setToDelete(null)
    if (env) act(remove, env, 'teamQuota.envs.deleted')
  }
  const reasons = (env: EnvEntry) => env.disabled_reasons || {}
  const actions = (env: EnvEntry) => (
    <SpaceBetween direction="horizontal" size="xs">
      {member && env.log_path && (
        <Button variant="link" onClick={() => setLogOf(env)}>
          {t('teamQuota.envs.actions.log')}
        </Button>
      )}
      {env.status === 'failed' &&
        (env.can.retry ? (
          <Button
            variant="link"
            disabled={retry.isLoading}
            onClick={() => act(retry, env, 'teamQuota.envs.retried')}
          >
            {t('teamQuota.envs.actions.retry')}
          </Button>
        ) : (
          <DisabledAction
            label={t('teamQuota.envs.actions.retry')}
            reason={reasons(env).retry}
          />
        ))}
      {env.can_change_visibility ? (
        <Button
          variant="link"
          ariaLabel={t('teamQuota.envs.actions.visibilityAria', {
            name: env.name,
          })}
          onClick={() => setToChange(env)}
        >
          {t('teamQuota.envs.actions.visibility')}
        </Button>
      ) : (
        <DisabledAction
          label={t('teamQuota.envs.actions.visibility')}
          reason={reasons(env).visibility}
        />
      )}
      {env.can.delete ? (
        <Button
          variant="link"
          ariaLabel={t('teamQuota.envs.actions.deleteAria', {
            id: `${env.name}:${env.version}`,
          })}
          disabled={remove.isLoading}
          onClick={() => setToDelete(env)}
        >
          {t('teamQuota.envs.actions.delete')}
        </Button>
      ) : (
        <DisabledAction
          label={t('teamQuota.envs.actions.delete')}
          reason={reasons(env).delete}
        />
      )}
    </SpaceBetween>
  )
  return (
    <SpaceBetween size="l">
      {isError && (
        <Alert type="error" header={t('teamQuota.envs.loadError')}>
          {errorMessage(error)}
        </Alert>
      )}
      {data?.warning && (
        <Alert type={usage?.level === 'exceeded' ? 'error' : 'warning'}>
          {data.warning}
        </Alert>
      )}
      {usage && (
        <Container
          header={
            <Header variant="h2">{t('teamQuota.envs.usage.title')}</Header>
          }
        >
          {usage.cap_gb ? (
            <ProgressBar
              value={Math.min(usage.percent ?? 0, 100)}
              status={usage.level === 'exceeded' ? 'error' : 'in-progress'}
              label={t('teamQuota.envs.usage.label', {team: ownTeam || '-'})}
              description={t('teamQuota.envs.usage.capped', {
                used: formatBytes(usage.total_bytes),
                cap: usage.cap_gb,
              })}
            />
          ) : (
            <Box>
              {t('teamQuota.envs.usage.uncapped', {
                used: formatBytes(usage.total_bytes),
              })}
            </Box>
          )}
        </Container>
      )}
      <Table<EnvEntry>
        variant="container"
        trackBy="id"
        loading={!!target && isLoading}
        loadingText={t('teamQuota.member.loading')}
        items={items}
        empty={
          <Box textAlign="center">
            {data?.envs.length
              ? t('teamQuota.envs.noMatch')
              : t('teamQuota.envs.empty')}
          </Box>
        }
        filter={
          <SpaceBetween direction="horizontal" size="xs">
            <Select
              ariaLabel={t('teamQuota.envs.filter.team')}
              selectedOption={
                teamOptions.find(option => option.value === teamFilter) ||
                teamOptions[0]
              }
              options={teamOptions}
              onChange={({detail}) =>
                setTeamFilter(detail.selectedOption.value || '')
              }
            />
            <TextFilter
              filteringText={filterText}
              filteringPlaceholder={t('teamQuota.envs.filter.placeholder')}
              filteringAriaLabel={t('teamQuota.envs.filter.placeholder')}
              countText={t('teamQuota.envs.filter.count', {
                count: items.length,
              })}
              onChange={({detail}) => setFilterText(detail.filteringText)}
            />
          </SpaceBetween>
        }
        header={
          <Header
            counter={`(${data?.envs.length ?? 0})`}
            description={t('teamQuota.envs.listDescription', {
              team: ownTeam || '-',
            })}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  iconName="refresh"
                  loading={isFetching}
                  onClick={() => refetch()}
                >
                  {t('teamQuota.member.refresh')}
                </Button>
                {member && (
                  <Button variant="primary" onClick={() => setCreating(true)}>
                    {t('teamQuota.envs.create.open')}
                  </Button>
                )}
              </SpaceBetween>
            }
          >
            {t('teamQuota.envs.listTitle')}
          </Header>
        }
        columnDefinitions={[
          {
            id: 'name',
            header: t('teamQuota.envs.columns.name'),
            cell: env => env.name,
          },
          {
            id: 'version',
            header: t('teamQuota.envs.columns.version'),
            cell: env => env.version,
          },
          {
            id: 'team',
            header: t('teamQuota.envs.columns.team'),
            cell: env => env.team,
          },
          {
            id: 'visibility',
            header: t('teamQuota.envs.columns.visibility'),
            cell: env => t(`teamQuota.envs.visibility.${env.visibility}`),
          },
          {
            id: 'kind',
            header: t('teamQuota.envs.columns.kind'),
            cell: env =>
              env.kind ? t(`teamQuota.envs.kinds.${env.kind}`) : '-',
          },
          {
            id: 'status',
            header: t('teamQuota.envs.columns.status'),
            cell: env => (
              <SpaceBetween size="xxxs">
                <EnvStatusBadge env={env} />
                {env.status === 'failed' && env.failure && (
                  <Box variant="small" color="text-status-error">
                    {env.failure}
                  </Box>
                )}
              </SpaceBetween>
            ),
          },
          {
            id: 'size',
            header: t('teamQuota.envs.columns.size'),
            cell: env => formatBytes(env.size_bytes),
          },
          {
            id: 'description',
            header: t('teamQuota.envs.columns.description'),
            cell: env =>
              [env.description, env.software].filter(Boolean).join(' · ') ||
              '-',
          },
          {
            id: 'owner',
            header: t('teamQuota.envs.columns.owner'),
            cell: env => env.owner || '-',
          },
          {id: 'actions', header: '', cell: actions, minWidth: 260},
        ]}
        footer={
          data?.truncated ? (
            <Box variant="small">{t('teamQuota.envs.truncated')}</Box>
          ) : undefined
        }
      />
      {logOf && member && (
        <LogViewer
          target={target}
          path={logOf.log_path}
          title={t('teamQuota.envs.logTitle', {
            id: `${logOf.name}:${logOf.version}`,
          })}
        />
      )}
      <CreateEnvModal
        target={target}
        limits={data?.limits}
        visible={creating}
        onDismiss={() => setCreating(false)}
      />
      {toChange && (
        <VisibilityModal
          target={target}
          env={toChange}
          onDismiss={() => setToChange(null)}
        />
      )}
      <Modal
        visible={!!toDelete}
        onDismiss={() => setToDelete(null)}
        header={t('teamQuota.envs.confirm.deleteTitle')}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setToDelete(null)}>
                {t('teamQuota.actions.cancel')}
              </Button>
              <Button variant="primary" onClick={confirmDelete}>
                {t('teamQuota.envs.confirm.confirm')}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {t('teamQuota.envs.confirm.deleteBody', {
          id: `${toDelete?.name}:${toDelete?.version}`,
          team: toDelete?.team,
        })}
      </Modal>
    </SpaceBetween>
  )
}

/** Members: their team's environments (they create, retry and delete their own). */
export default function EnvsPage() {
  const {target} = useMemberTarget()
  return (
    <MemberLayout page="envs">
      <MemberTeamSelect />
      <EnvsContent target={target} />
    </MemberLayout>
  )
}

/** Team leads (and admins) who are not members: view, delete and change visibility. */
export function LeadEnvsPage() {
  const {target} = useLeadTarget()
  return (
    <LeadLayout page="envs">
      <EnvsContent target={target} />
    </LeadLayout>
  )
}
