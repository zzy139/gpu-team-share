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
  Checkbox,
  ColumnLayout,
  Container,
  ExpandableSection,
  Form,
  FormField,
  Header,
  Input,
  Modal,
  RadioGroup,
  Select,
  SelectProps,
  SpaceBetween,
  Table,
  TableProps,
  Tabs,
  Textarea,
} from '@cloudscape-design/components'
import React from 'react'
import {useTranslation} from 'react-i18next'
import {useNavigate, useSearchParams} from 'react-router-dom'
import {notify} from '../../model'
import {errorMessage} from '../api'
import {CapacityStatusBadge} from '../CapacityStatus'
import {formatSpec} from '../format'
import {MultiTypeQueuesAlert} from '../TypeLimitsTable'
import {parseInteger} from '../validation'
import {GetResubmit, utf8Length} from './api'
import MemberLayout, {MemberTeamSelect} from './MemberLayout'
import {
  useCreateTemplateMutation,
  useMemberOptionsQuery,
  useMemberTarget,
  useSubmitJobMutation,
  useTemplatesQuery,
} from './queries'
import {TemplatesContent} from './TemplatesPage'
import {
  EnvOption,
  EnvRef,
  JobTemplate,
  MemberType,
  ResubmitJob,
  SubmitJobRequest,
} from './types'
export const SCRIPT_MAX_BYTES = 65536
export const BORROW_MAX_MINUTES = 240
// Mirrors TIME_MAX_MINUTES of api/teams/validation.py (an optional limit set in 高级选项).
export const TIME_MAX_MINUTES = 10080
const JOB_NAME_RE = /^[A-Za-z0-9._-]{1,64}$/
const PATH_RE = /^\/[^\u0000-\u001f\u007f]{0,1023}$/
const TEMPLATE_NAME_RE = /^[^\u0000-\u001f\u007f]{1,64}$/
const SAMPLE_SCRIPT = `#!/bin/bash
#SBATCH --output=%x-%j.out
nvidia-smi
`
const NO_ENV = '-'
type ScriptMode = 'content' | 'path'
type Errors = Record<string, string | null>

export function envKey(env: EnvRef | null | undefined): string {
  return env ? `${env.scope}/${env.team}/${env.name}/${env.version}` : NO_ENV
}

function envLabel(env: EnvOption) {
  return `${env.name}:${env.version}`
}

function envDescription(env: EnvOption, team: string | undefined, t: T) {
  const parts = [env.kind, env.description]
  if (env.detected?.cuda) parts.push(`CUDA ${env.detected.cuda}`)
  if (env.detected?.python) parts.push(`Python ${env.detected.python}`)
  if (env.team !== team)
    parts.push(t('teamQuota.member.submit.envOfTeam', {team: env.team}))
  return parts.filter(Boolean).join(' · ')
}

type T = (key: string, options?: any) => string

/** Runtime environment options: "none", then the team's environments and the 所有人 ones of other teams. */
export function envOptions(
  envs: EnvOption[],
  t: T,
  team?: string,
): SelectProps.Options {
  const toOption = (env: EnvOption): SelectProps.Option => ({
    value: envKey(env),
    label: envLabel(env),
    description: envDescription(env, team, t),
  })
  const own = envs.filter(env => env.team === team).map(toOption)
  const others = envs.filter(env => env.team !== team).map(toOption)
  return [
    {value: NO_ENV, label: t('teamQuota.member.submit.envNone')},
    ...(own.length
      ? [{label: t('teamQuota.member.submit.envTeam'), options: own}]
      : []),
    ...(others.length
      ? [{label: t('teamQuota.member.submit.envOthers'), options: others}]
      : []),
  ]
}

interface FormState {
  instanceType: string | null
  nodes: string
  /** 高级选项: optional limit; empty = no limit (own quota) or the borrow cap. */
  minutes: string
  borrow: boolean
  jobName: string
  workdir: string
  mode: ScriptMode
  scriptPath: string
  script: string
  env: string
}

const DEFAULT_FORM: FormState = {
  instanceType: null,
  nodes: '1',
  minutes: '',
  borrow: false,
  jobName: '',
  workdir: '',
  mode: 'content',
  scriptPath: '',
  script: SAMPLE_SCRIPT,
  env: NO_ENV,
}

/** Form values from a resubmit or template body (CPU, memory and GPUs per machine are ignored: whole machines). */
export function formFromJob(job: ResubmitJob): FormState {
  const text = (value: number | undefined | null) =>
    value === undefined || value === null ? '' : String(value)
  return {
    ...DEFAULT_FORM,
    instanceType: job.instance_type,
    nodes: text(job.nodes) || '1',
    minutes: text(job.time_minutes),
    borrow: !!job.borrow,
    jobName: job.job_name || '',
    workdir: job.workdir || '',
    mode: job.script_path ? 'path' : 'content',
    scriptPath: job.script_path || '',
    script: job.script_content ?? (job.script_path ? '' : SAMPLE_SCRIPT),
    env: envKey(job.env),
  }
}

/** The team has machines of this type (limit > 0); other types can only be borrowed. */
export function hasQuota(type: MemberType) {
  return type.has_quota ?? type.limit > 0
}

function gpuText(type: MemberType) {
  if (!type.gpus) return '-'
  return `${type.gpus}× ${type.gpu_model || 'GPU'}`
}

function TypeTable({
  types,
  selected,
  onSelect,
  loading,
  borrowOnly,
}: {
  types: MemberType[]
  selected: string | null
  onSelect: (instanceType: string) => void
  loading: boolean
  borrowOnly: boolean
}) {
  const {t} = useTranslation()
  const columns: TableProps.ColumnDefinition<MemberType>[] = [
    {
      id: 'type',
      header: t('teamQuota.member.submit.instanceType'),
      cell: type => type.instance_type,
    },
    {
      id: 'gpu',
      header: t('teamQuota.member.submit.gpuModel'),
      cell: gpuText,
    },
    {
      id: 'gpuMemory',
      header: t('teamQuota.member.submit.gpuMemory'),
      cell: type => (type.gpu_memory_gb ? `${type.gpu_memory_gb} GB` : '-'),
    },
    {
      id: 'vcpus',
      header: t('teamQuota.member.submit.vcpus'),
      cell: type => type.vcpus ?? '-',
    },
    {
      id: 'memory',
      header: t('teamQuota.member.submit.memory'),
      cell: type =>
        type.memory_gib === null || type.memory_gib === undefined
          ? '-'
          : `${type.memory_gib} GiB`,
    },
  ]
  if (!borrowOnly)
    columns.push(
      {
        id: 'limit',
        header: t('teamQuota.member.submit.limit'),
        cell: type => type.limit,
      },
      {
        id: 'running',
        header: t('teamQuota.member.submit.running'),
        cell: type => type.running,
      },
      {
        id: 'remaining',
        header: t('teamQuota.member.submit.remaining'),
        cell: type => type.remaining,
      },
    )
  columns.push({
    id: 'status',
    header: t('teamQuota.member.submit.status'),
    cell: type => <CapacityStatusBadge status={type.status} />,
  })
  return (
    <Table<MemberType>
      variant="embedded"
      trackBy="instance_type"
      loading={loading}
      loadingText={t('teamQuota.member.loading')}
      items={types}
      selectionType="single"
      selectedItems={types.filter(type => type.instance_type === selected)}
      onSelectionChange={({detail}) =>
        detail.selectedItems[0] &&
        onSelect(detail.selectedItems[0].instance_type)
      }
      ariaLabels={{
        selectionGroupLabel: borrowOnly
          ? t('teamQuota.member.submit.borrowOnlyTitle')
          : t('teamQuota.member.submit.instanceType'),
        itemSelectionLabel: (_, item) => item.instance_type,
      }}
      empty={
        <Box textAlign="center">
          {borrowOnly
            ? t('teamQuota.member.submit.noBorrowTypes')
            : t('teamQuota.member.submit.noTypes')}
        </Box>
      }
      columnDefinitions={columns}
    />
  )
}

export default function SubmitJobPage() {
  const {t} = useTranslation()
  const navigate = useNavigate()
  const [search, setSearch] = useSearchParams()
  const {target} = useMemberTarget()
  const options = useMemberOptionsQuery(target)
  const templates = useTemplatesQuery(target)
  const mutation = useSubmitJobMutation(target)
  const saveTemplate = useCreateTemplateMutation(target)
  const limits = options.data?.limits
  const borrowMax = limits?.borrow_max_minutes ?? BORROW_MAX_MINUTES
  const types = React.useMemo(() => options.data?.types || [], [options.data])
  const ownTypes = React.useMemo(() => types.filter(hasQuota), [types])
  const borrowTypes = React.useMemo(
    () => types.filter(type => !hasQuota(type) && type.borrow_allowed),
    [types],
  )
  const envs = React.useMemo(() => options.data?.envs || [], [options.data])
  const [form, setForm] = React.useState<FormState>(DEFAULT_FORM)
  const [errors, setErrors] = React.useState<Errors>({})
  const [prefill, setPrefill] = React.useState<string | null>(null)
  const [templateName, setTemplateName] = React.useState<string | null>(null)
  const [advancedOpen, setAdvancedOpen] = React.useState(false)
  const [othersOpen, setOthersOpen] = React.useState(false)
  const set = (change: Partial<FormState>) =>
    setForm(prev => ({...prev, ...change}))
  const tab = search.get('tab') === 'templates' ? 'templates' : 'submit'

  const fill = React.useCallback(
    (job: ResubmitJob) => {
      const next = formFromJob(job)
      setForm(next)
      setErrors({})
      if (next.minutes) setAdvancedOpen(true)
      const type = types.find(item => item.instance_type === next.instanceType)
      if (type && !hasQuota(type)) setOthersOpen(true)
    },
    [types],
  )

  // ?resubmit=<job id> or ?template=<id> prefill the form once.
  const resubmitId = search.get('resubmit')
  const templateId = search.get('template')
  React.useEffect(() => {
    if (!target || !resubmitId || prefill === `job:${resubmitId}`) return
    setPrefill(`job:${resubmitId}`)
    GetResubmit(target, resubmitId)
      .then(result => {
        fill(result.job)
        notify(
          t('teamQuota.member.submit.prefilledJob', {jobId: result.job_id}),
          'info',
        )
      })
      .catch(err => notify(errorMessage(err), 'error'))
  }, [target, resubmitId, prefill, t, fill])
  React.useEffect(() => {
    if (!templateId || prefill === `tpl:${templateId}`) return
    const template = templates.data?.templates.find(
      item => item.id === templateId,
    )
    if (!template) return
    setPrefill(`tpl:${templateId}`)
    fill(template.job)
  }, [templateId, templates.data, prefill, fill])
  React.useEffect(() => {
    if (form.instanceType || !ownTypes.length) return
    const first = ownTypes.find(type => type.remaining > 0) || ownTypes[0]
    set({instanceType: first.instance_type})
  }, [form.instanceType, ownTypes])
  React.useEffect(() => {
    // without own quota the borrow-only types are the only choice
    if (!options.isLoading && types.length && !ownTypes.length)
      setOthersOpen(true)
  }, [options.isLoading, types.length, ownTypes.length])

  const applyTemplate = (template: JobTemplate) => {
    setPrefill(`tpl:${template.id}`)
    fill(template.job)
    setSearch({})
  }

  const selectedType = types.find(
    type => type.instance_type === form.instanceType,
  )
  // a type without own quota can only run as a borrowed job
  const borrowOnly = !!selectedType && !hasQuota(selectedType)
  const borrow = form.borrow || borrowOnly
  const scriptBytes = utf8Length(form.script)
  const range = (value: string, min: number, max: number) => {
    const parsed = parseInteger(value)
    return parsed === null || parsed < min || parsed > max
      ? t('teamQuota.member.submit.errors.range', {min, max})
      : null
  }

  /** Validated request body, or null (errors are shown). */
  const buildBody = (): SubmitJobRequest | null => {
    const newErrors: Errors = {
      instanceType: selectedType
        ? null
        : t('teamQuota.member.submit.errors.instanceType'),
      nodes: range(form.nodes, 1, limits?.nodes_max ?? 64),
      minutes: form.minutes.trim()
        ? range(form.minutes, 1, TIME_MAX_MINUTES)
        : null,
      jobName:
        form.jobName && !JOB_NAME_RE.test(form.jobName)
          ? t('teamQuota.member.submit.errors.jobName')
          : null,
      workdir:
        form.workdir && !PATH_RE.test(form.workdir)
          ? t('teamQuota.member.submit.errors.path')
          : null,
      scriptPath:
        form.mode === 'path' && !PATH_RE.test(form.scriptPath)
          ? t('teamQuota.member.submit.errors.path')
          : null,
      script:
        form.mode === 'content'
          ? !form.script.trim()
            ? t('teamQuota.member.submit.errors.scriptEmpty')
            : scriptBytes > SCRIPT_MAX_BYTES
            ? t('teamQuota.member.submit.errors.scriptTooLarge', {
                max: SCRIPT_MAX_BYTES,
              })
            : form.script.includes('\u0000')
            ? t('teamQuota.member.submit.errors.scriptNul')
            : null
          : null,
    }
    setErrors(newErrors)
    if (newErrors.minutes) setAdvancedOpen(true)
    if (Object.values(newErrors).some(Boolean)) return null
    const body: SubmitJobRequest = {
      team: target?.team,
      instance_type: selectedType!.instance_type,
      nodes: parseInteger(form.nodes)!,
      borrow,
    }
    const minutes = form.minutes.trim() ? parseInteger(form.minutes)! : null
    // own quota: no limit unless set in 高级选项; borrowed: at most 4 hours (also enforced by Slurm)
    if (borrow) body.time_minutes = Math.min(minutes ?? borrowMax, borrowMax)
    else if (minutes !== null) body.time_minutes = minutes
    const env = envs.find(item => envKey(item) === form.env)
    body.env = env
      ? {scope: env.scope, team: env.team, name: env.name, version: env.version}
      : null
    if (form.jobName) body.job_name = form.jobName
    if (form.workdir) body.workdir = form.workdir
    if (form.mode === 'path') body.script_path = form.scriptPath
    else body.script_content = form.script
    return body
  }

  const submit = () => {
    const body = buildBody()
    if (!body) return
    mutation.mutate(body, {
      onSuccess: result => {
        notify(
          t('teamQuota.member.submit.submitted', {jobId: result.job_id}),
          'success',
        )
        navigate(`/my-jobs/${encodeURIComponent(result.job_id)}`)
      },
      onError: err => notify(errorMessage(err), 'error'),
    })
  }

  const saveAsTemplate = () => {
    const name = (templateName || '').trim()
    if (!TEMPLATE_NAME_RE.test(name)) {
      setErrors(prev => ({
        ...prev,
        templateName: t('teamQuota.member.templates.errors.name'),
      }))
      return
    }
    const body = buildBody()
    if (!body) {
      setTemplateName(null)
      return
    }
    const {team, ...job} = body
    saveTemplate.mutate(
      {name, job},
      {
        onSuccess: () => {
          notify(t('teamQuota.member.templates.saved', {name}), 'success')
          setTemplateName(null)
        },
        onError: err => notify(errorMessage(err), 'error'),
      },
    )
  }

  const allEnvOptions = envOptions(envs, t, options.data?.team || target?.team)
  const selectedEnvOption =
    (allEnvOptions
      .flatMap(option =>
        'options' in option ? option.options : [option as SelectProps.Option],
      )
      .find(option => option.value === form.env) as SelectProps.Option) || null
  const templateOptions = (templates.data?.templates || []).map(item => ({
    value: item.id,
    label: item.name,
    description: item.job.instance_type || undefined,
  }))
  const minutesValue = parseInteger(form.minutes)

  const submitForm = (
    <Form
      actions={
        <SpaceBetween direction="horizontal" size="xs">
          <Button onClick={() => navigate('/my-jobs')}>
            {t('teamQuota.actions.cancel')}
          </Button>
          <Button onClick={() => setTemplateName(form.jobName || '')}>
            {t('teamQuota.member.templates.saveAs')}
          </Button>
          <Button
            variant="primary"
            loading={mutation.isLoading}
            onClick={submit}
          >
            {t('teamQuota.member.submit.submit')}
          </Button>
        </SpaceBetween>
      }
    >
      <SpaceBetween size="l">
        {templateOptions.length > 0 && (
          <FormField
            label={t('teamQuota.member.submit.fromTemplate')}
            description={t('teamQuota.member.submit.fromTemplateDescription')}
          >
            <Select
              ariaLabel={t('teamQuota.member.submit.fromTemplate')}
              placeholder={t('teamQuota.member.submit.fromTemplatePlaceholder')}
              selectedOption={
                templateOptions.find(
                  option => prefill === `tpl:${option.value}`,
                ) || null
              }
              options={templateOptions}
              onChange={({detail}) => {
                const template = templates.data?.templates.find(
                  item => item.id === detail.selectedOption.value,
                )
                if (template) applyTemplate(template)
              }}
            />
          </FormField>
        )}
        <Container
          header={
            <Header
              variant="h2"
              description={t('teamQuota.member.submit.wholeMachineHint')}
            >
              {t('teamQuota.member.submit.instanceType')}
            </Header>
          }
        >
          <FormField errorText={errors.instanceType} stretch>
            <SpaceBetween size="m">
              <TypeTable
                types={ownTypes}
                selected={form.instanceType}
                onSelect={instanceType => set({instanceType})}
                loading={options.isLoading}
                borrowOnly={false}
              />
              {borrowTypes.length > 0 && (
                <ExpandableSection
                  variant="container"
                  headerText={t('teamQuota.member.submit.borrowOnlyTitle')}
                  headerDescription={t(
                    'teamQuota.member.submit.borrowOnlyDescription',
                    {hours: borrowMax / 60},
                  )}
                  expanded={othersOpen}
                  onChange={({detail}) => setOthersOpen(detail.expanded)}
                >
                  <TypeTable
                    types={borrowTypes}
                    selected={form.instanceType}
                    onSelect={instanceType => set({instanceType})}
                    loading={false}
                    borrowOnly
                  />
                </ExpandableSection>
              )}
            </SpaceBetween>
          </FormField>
        </Container>
        <Container
          header={
            <Header
              variant="h2"
              description={
                selectedType
                  ? t('teamQuota.member.submit.selectedSpec', {
                      type: selectedType.instance_type,
                      spec: formatSpec(selectedType),
                    })
                  : undefined
              }
            >
              {t('teamQuota.member.submit.resources')}
            </Header>
          }
        >
          <SpaceBetween size="l">
            <ColumnLayout columns={2}>
              <FormField
                label={t('teamQuota.member.submit.nodes')}
                description={t('teamQuota.member.submit.nodesDescription')}
                errorText={errors.nodes}
              >
                <Input
                  type="number"
                  ariaLabel={t('teamQuota.member.submit.nodes')}
                  value={form.nodes}
                  onChange={({detail}) => set({nodes: detail.value})}
                />
              </FormField>
              <FormField
                label={t('teamQuota.member.submit.env')}
                description={
                  options.data?.envs_error
                    ? t('teamQuota.member.submit.envError', {
                        message: options.data.envs_error,
                      })
                    : t('teamQuota.member.submit.envDescription')
                }
              >
                <Select
                  ariaLabel={t('teamQuota.member.submit.env')}
                  selectedOption={selectedEnvOption}
                  options={allEnvOptions}
                  statusType={options.isLoading ? 'loading' : 'finished'}
                  onChange={({detail}) =>
                    set({env: detail.selectedOption.value || NO_ENV})
                  }
                />
              </FormField>
              <FormField
                label={t('teamQuota.member.submit.jobName')}
                errorText={errors.jobName}
              >
                <Input
                  ariaLabel={t('teamQuota.member.submit.jobName')}
                  value={form.jobName}
                  onChange={({detail}) => set({jobName: detail.value})}
                />
              </FormField>
              <FormField
                label={t('teamQuota.member.submit.workdir')}
                description={t('teamQuota.member.submit.workdirDescription', {
                  root: options.data?.team_root || '-',
                })}
                errorText={errors.workdir}
              >
                <Input
                  ariaLabel={t('teamQuota.member.submit.workdir')}
                  value={form.workdir}
                  placeholder={options.data?.team_root}
                  onChange={({detail}) => set({workdir: detail.value})}
                />
              </FormField>
              <FormField
                label={t('teamQuota.member.submit.borrowLabel')}
                description={
                  borrowOnly
                    ? t('teamQuota.member.submit.borrowForced')
                    : t('teamQuota.member.submit.borrowDescription', {
                        hours: borrowMax / 60,
                      })
                }
              >
                <Checkbox
                  checked={borrow}
                  disabled={borrowOnly}
                  onChange={({detail}) => set({borrow: detail.checked})}
                >
                  {t('teamQuota.member.submit.borrow')}
                </Checkbox>
              </FormField>
            </ColumnLayout>
            {borrow && (
              <Alert type="warning">
                {t('teamQuota.member.submit.borrowLimit', {
                  hours: borrowMax / 60,
                })}
              </Alert>
            )}
            <ExpandableSection
              headerText={t('teamQuota.member.submit.advanced')}
              expanded={advancedOpen}
              onChange={({detail}) => setAdvancedOpen(detail.expanded)}
            >
              <FormField
                label={t('teamQuota.member.submit.maxMinutes')}
                description={
                  borrow
                    ? t('teamQuota.member.submit.maxMinutesBorrow', {
                        max: borrowMax,
                      })
                    : t('teamQuota.member.submit.maxMinutesDescription')
                }
                constraintText={
                  borrow && minutesValue !== null && minutesValue > borrowMax
                    ? t('teamQuota.member.submit.maxMinutesCapped', {
                        max: borrowMax,
                      })
                    : undefined
                }
                errorText={errors.minutes}
              >
                <Input
                  type="number"
                  ariaLabel={t('teamQuota.member.submit.maxMinutes')}
                  placeholder={t(
                    'teamQuota.member.submit.maxMinutesPlaceholder',
                  )}
                  value={form.minutes}
                  onChange={({detail}) => set({minutes: detail.value})}
                />
              </FormField>
            </ExpandableSection>
          </SpaceBetween>
        </Container>
        <Container
          header={
            <Header variant="h2">{t('teamQuota.member.submit.script')}</Header>
          }
        >
          <SpaceBetween size="m">
            <RadioGroup
              value={form.mode}
              onChange={({detail}) => set({mode: detail.value as ScriptMode})}
              items={[
                {
                  value: 'content',
                  label: t('teamQuota.member.submit.modeContent'),
                },
                {value: 'path', label: t('teamQuota.member.submit.modePath')},
              ]}
            />
            {form.mode === 'path' ? (
              <FormField
                label={t('teamQuota.member.submit.scriptPath')}
                description={t('teamQuota.member.submit.scriptPathDescription')}
                errorText={errors.scriptPath}
              >
                <Input
                  ariaLabel={t('teamQuota.member.submit.scriptPath')}
                  value={form.scriptPath}
                  onChange={({detail}) => set({scriptPath: detail.value})}
                />
              </FormField>
            ) : (
              <FormField
                label={t('teamQuota.member.submit.scriptContent')}
                description={t(
                  'teamQuota.member.submit.scriptContentDescription',
                )}
                constraintText={t('teamQuota.member.submit.bytes', {
                  bytes: scriptBytes,
                  max: SCRIPT_MAX_BYTES,
                })}
                errorText={errors.script}
              >
                <Textarea
                  rows={14}
                  value={form.script}
                  onChange={({detail}) => set({script: detail.value})}
                />
              </FormField>
            )}
            <Box variant="small">{t('teamQuota.member.submit.runAs')}</Box>
          </SpaceBetween>
        </Container>
      </SpaceBetween>
    </Form>
  )

  return (
    <MemberLayout page="submitJob">
      <MemberTeamSelect />
      {options.isError && (
        <Alert type="error">{errorMessage(options.error)}</Alert>
      )}
      <MultiTypeQueuesAlert queues={options.data?.unmanaged_queues} />
      <Tabs
        activeTabId={tab}
        onChange={({detail}) =>
          setSearch(
            detail.activeTabId === 'templates' ? {tab: 'templates'} : {},
          )
        }
        tabs={[
          {
            id: 'submit',
            label: t('teamQuota.member.submit.tabSubmit'),
            content: submitForm,
          },
          {
            id: 'templates',
            label: t('teamQuota.member.submit.tabTemplates'),
            content: <TemplatesContent onUse={applyTemplate} />,
          },
        ]}
      />
      <Modal
        visible={templateName !== null}
        onDismiss={() => setTemplateName(null)}
        header={t('teamQuota.member.templates.saveAs')}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setTemplateName(null)}>
                {t('teamQuota.actions.cancel')}
              </Button>
              <Button
                variant="primary"
                loading={saveTemplate.isLoading}
                onClick={saveAsTemplate}
              >
                {t('teamQuota.actions.save')}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField
          label={t('teamQuota.member.templates.name')}
          errorText={errors.templateName}
        >
          <Input
            ariaLabel={t('teamQuota.member.templates.name')}
            value={templateName || ''}
            onChange={({detail}) => setTemplateName(detail.value)}
          />
        </FormField>
      </Modal>
    </MemberLayout>
  )
}
