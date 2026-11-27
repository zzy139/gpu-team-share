// Editing a cluster created by the HPC solution templates and adding one queue must send ONLY the new queue.
// @ts-expect-error no types for js-yaml
import {dump, load} from 'js-yaml'
import * as fs from 'fs'
import * as path from 'path'
import {
  defaultQueueNodeSetup,
  defaultQueueSubnets,
  equivalent,
  onlyDefaults,
  preserveUnchanged,
  queueInstanceTypes,
} from '../editPreserve'
import {normalizeIntegers} from '../util'
import {newQueue} from '../Queues/Queues'
import {createComputeResource} from '../Queues/MultiInstanceComputeResource'

const FIXTURE = path.join(__dirname, 'fixtures', 'hpc-cluster-config.yaml')

/** CloudFormation passes custom resource properties as strings: the deployed config has '100', 'true'. */
function asDeployed(value: any): any {
  if (Array.isArray(value)) return value.map(asDeployed)
  if (value && typeof value === 'object')
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, asDeployed(v)]),
    )
  return typeof value === 'number' || typeof value === 'boolean'
    ? String(value)
    : value
}

/** What the wizard pages fill in on their own (seen in the live dry run of run 7). */
function wizardDefaults(config: any) {
  const c = normalizeIntegers(JSON.parse(JSON.stringify(config)))
  c.HeadNode.Ssh.KeyName = 'first-key-pair'
  c.HeadNode.LocalStorage.RootVolume.VolumeType = 'gp3'
  c.DirectoryService = {
    ...(c.DirectoryService || {}),
    GenerateSshKeysForUsers: true,
  }
  for (const q of c.Scheduling.SlurmQueues) {
    q.ComputeSettings = {
      LocalStorage: {
        RootVolume: {
          VolumeType: 'gp3',
          ...(q.ComputeSettings?.LocalStorage?.RootVolume || {}),
        },
      },
    }
    q.Networking.PlacementGroup = {Enabled: false}
    q.AllocationStrategy = q.AllocationStrategy || 'lowest-price'
  }
  return c
}

describe('preserveUnchanged', () => {
  const deployed = asDeployed(load(fs.readFileSync(FIXTURE, 'utf8')))

  it('round-trips an untouched configuration byte for byte', () => {
    expect(dump(preserveUnchanged(deployed, wizardDefaults(deployed)))).toEqual(
      dump(deployed),
    )
  })

  it('adds one queue and nothing else', () => {
    const edited = wizardDefaults(deployed)
    const queues = edited.Scheduling.SlurmQueues
    const cr = createComputeResource('g6-xlarge', 0)
    cr.Instances = [{InstanceType: 'g6.xlarge'}]
    queues.push(
      newQueue(
        'g6-xlarge',
        queues,
        edited.HeadNode.Networking.SubnetId,
        {AllocationStrategy: 'lowest-price'},
        cr,
      ),
    )
    const out = preserveUnchanged(deployed, edited)
    const added = out.Scheduling.SlurmQueues.filter(
      (q: any) => q.Name === 'g6-xlarge',
    )
    expect(added).toHaveLength(1)
    expect(added[0].JobExclusiveAllocation).toBe(true)
    expect(added[0].Networking.SubnetIds).toEqual(
      deployed.Scheduling.SlurmQueues[0].Networking.SubnetIds,
    )
    expect(added[0].Networking.SubnetIds).not.toContain(
      deployed.HeadNode.Networking.SubnetId,
    )
    // node setup of the existing queues (Enroot/Pyxis script and its S3 access) is inherited
    expect(added[0].CustomActions).toEqual(
      deployed.Scheduling.SlurmQueues[1].CustomActions,
    )
    expect(added[0].CustomActions.OnNodeStart.Script).toMatch(
      /node-enroot\.sh$/,
    )
    expect(added[0].Iam).toEqual(deployed.Scheduling.SlurmQueues[1].Iam)
    expect(added[0].ComputeResources).toEqual([
      {
        Name: 'g6-xlarge-cr-1',
        Instances: [{InstanceType: 'g6.xlarge'}],
        MinCount: 0,
        MaxCount: 2,
      },
    ])
    out.Scheduling.SlurmQueues = out.Scheduling.SlurmQueues.filter(
      (q: any) => q.Name !== 'g6-xlarge',
    )
    expect(dump(out)).toEqual(dump(deployed))
  })

  it('keeps real edits: a changed value and an added setting', () => {
    const edited = wizardDefaults(deployed)
    edited.Scheduling.SlurmQueues[0].ComputeResources[0].MaxCount = 3
    edited.Scheduling.SlurmQueues[0].CapacityType = 'SPOT'
    const out = preserveUnchanged(deployed, edited)
    expect(out.Scheduling.SlurmQueues[0].ComputeResources[0].MaxCount).toBe(3)
    expect(out.Scheduling.SlurmQueues[0].CapacityType).toBe('SPOT')
    expect(out.Scheduling.SlurmQueues[1]).toEqual(
      deployed.Scheduling.SlurmQueues[1],
    )
  })

  it('keeps removals of queues', () => {
    const edited = wizardDefaults(deployed)
    edited.Scheduling.SlurmQueues.pop()
    expect(
      preserveUnchanged(deployed, edited).Scheduling.SlurmQueues,
    ).toHaveLength(deployed.Scheduling.SlurmQueues.length - 1)
  })
})

describe('helpers', () => {
  it('compares numbers and booleans by value', () => {
    expect(equivalent({a: '10', b: 'true'}, {a: 10, b: true})).toBe(true)
    expect(equivalent({a: '10'}, {a: 11})).toBe(false)
  })
  it('knows the wizard defaults', () => {
    expect(onlyDefaults('PlacementGroup', {Enabled: false})).toBe(true)
    expect(onlyDefaults('PlacementGroup', {Enabled: true})).toBe(false)
    expect(
      onlyDefaults('ComputeSettings', {
        LocalStorage: {RootVolume: {VolumeType: 'gp3'}},
      }),
    ).toBe(true)
    expect(
      onlyDefaults('ComputeSettings', {
        LocalStorage: {RootVolume: {Size: 200}},
      }),
    ).toBe(false)
  })
  it('picks the subnets most queues use, else the head node subnet', () => {
    const q = (s: string[]) => ({Networking: {SubnetIds: s}})
    expect(
      defaultQueueSubnets([q(['a', 'b']), q(['b', 'a']), q(['c'])], 'h'),
    ).toEqual(['a', 'b'])
    expect(defaultQueueSubnets([], 'h')).toEqual(['h'])
  })
  it('copies the node setup most queues use, as a copy', () => {
    const a = {
      CustomActions: {OnNodeStart: {Script: 's3://b/node.sh'}},
      Iam: {S3Access: [{BucketName: 'b'}]},
    }
    const queues = [
      JSON.parse(JSON.stringify(a)),
      {CustomActions: {OnNodeStart: {Script: 's3://b/other.sh'}}},
      JSON.parse(JSON.stringify(a)),
      {Name: 'plain'},
    ]
    const setup = defaultQueueNodeSetup(queues)
    expect(setup).toEqual(a)
    setup.Iam.S3Access.push({BucketName: 'x'})
    expect(queues[0].Iam.S3Access).toHaveLength(1)
    expect(defaultQueueNodeSetup([{Name: 'plain'}])).toEqual({})
  })
  it('lists the instance types of a queue', () => {
    expect(
      queueInstanceTypes({
        ComputeResources: [
          {
            Instances: [
              {InstanceType: 'g6.xlarge'},
              {InstanceType: 'g6.2xlarge'},
            ],
          },
        ],
      }),
    ).toEqual(['g6.xlarge', 'g6.2xlarge'])
  })
})
