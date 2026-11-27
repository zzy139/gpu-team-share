import {normalizeIntegers} from '../util'

describe('normalizeIntegers', () => {
  it('turns integer strings of numeric keys into numbers and leaves everything else alone', () => {
    const config = {
      HeadNode: {LocalStorage: {RootVolume: {Size: '100', Encrypted: 'true'}}},
      Scheduling: {
        SlurmQueues: [
          {
            Name: '100',
            ComputeResources: [{Name: 'cr', MinCount: '0', MaxCount: '8'}],
            CustomActions: {
              OnNodeStart: {Script: 's3://b/x.sh', Args: ['100', 'us-west-2']},
            },
          },
        ],
        SlurmSettings: {CustomSlurmSettings: [{PriorityWeightJobSize: '0'}]},
      },
      Tags: [{Key: 'Size', Value: '0112'}],
    }
    const out = normalizeIntegers(config)
    expect(out.HeadNode.LocalStorage.RootVolume).toEqual({
      Size: 100,
      Encrypted: 'true',
    })
    expect(out.Scheduling.SlurmQueues[0].ComputeResources[0]).toEqual({
      Name: 'cr',
      MinCount: 0,
      MaxCount: 8,
    })
    expect(out.Scheduling.SlurmQueues[0].Name).toBe('100')
    expect(
      out.Scheduling.SlurmQueues[0].CustomActions.OnNodeStart.Args,
    ).toEqual(['100', 'us-west-2'])
    expect(
      out.Scheduling.SlurmSettings.CustomSlurmSettings[0].PriorityWeightJobSize,
    ).toBe('0')
    expect(out.Tags).toEqual([{Key: 'Size', Value: '0112'}])
  })
})
