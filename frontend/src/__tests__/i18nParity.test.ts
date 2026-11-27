import en from '../../locales/en/strings.json'
import zh from '../../locales/zh/strings.json'
import i18n from '../i18n'

type Tree = {[key: string]: string | Tree}

function flatten(tree: Tree, prefix = ''): Record<string, string> {
  return Object.entries(tree).reduce<Record<string, string>>(
    (out, [key, value]) => {
      const path = prefix ? `${prefix}.${key}` : key
      if (typeof value === 'string') out[path] = value
      else Object.assign(out, flatten(value, path))
      return out
    },
    {},
  )
}

// Chinese characters, CJK punctuation and full-width forms.
const CJK = /[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/
// Terms that stay as they are in Chinese UI text.
const KEPT_TERMS = new Set(['vCPU', 'GPU', 'QOS', 'Conda', 'Dockerfile'])
const enKeys = flatten(en as Tree)
const zhKeys = flatten(zh as Tree)
// Interpolations, nested $t() references and Trans tags must survive the translation.
const TOKENS =
  /\{\{[^}]*\}\}|\$t\([^)]*[)}]|<\/?\d+>|<\d+\/>|<\/?(?:strong|br|i|code|p)\/?>/g
const tokens = (text: string) => (text.match(TOKENS) || []).sort()

describe('i18n', () => {
  it('uses Chinese by default and English as the fallback', () => {
    expect(i18n.language).toBe('zh')
    expect(i18n.options.fallbackLng).toEqual(['en'])
  })

  it('has every English key in Chinese', () => {
    const missing = Object.keys(enKeys).filter(key => !(key in zhKeys))
    expect(missing).toEqual([])
  })

  it('has no Chinese key that English lacks', () => {
    const extra = Object.keys(zhKeys).filter(key => !(key in enKeys))
    expect(extra).toEqual([])
  })

  it('keeps the placeholders and tags of every string', () => {
    const changed = Object.keys(enKeys).filter(
      key => tokens(enKeys[key]).join('|') !== tokens(zhKeys[key]).join('|'),
    )
    expect(changed).toEqual([])
  })

  it.each(['global.menu', 'teamQuota', 'teamUsage', 'users', 'cluster.list'])(
    'translates %s into Chinese',
    section => {
      // The menu header is the product name.
      const english = Object.entries(zhKeys).filter(
        ([key, value]) =>
          key.startsWith(`${section}.`) &&
          key !== 'global.menu.header' &&
          !CJK.test(value) &&
          !KEPT_TERMS.has(value) &&
          // Values that are identical in both languages (product names, units, formats, examples).
          value !== enKeys[key],
      )
      expect(english).toEqual([])
      const translated = Object.entries(zhKeys).filter(
        ([key, value]) => key.startsWith(`${section}.`) && CJK.test(value),
      )
      expect(translated.length).toBeGreaterThan(0)
    },
  )

  it('shows the menus in Chinese', () => {
    ;[
      'clusters',
      'capacity',
      'images',
      'teams',
      'teamUsage',
      'users',
      'audit',
      'myTeam',
      'myJobs',
      'submitJob',
      'envs',
    ].forEach(key => expect(i18n.t(`global.menu.${key}`)).toMatch(CJK))
    expect(i18n.t('global.menu.users')).toBe('管理员账号')
    expect(i18n.t('global.menu.capacity')).toBe('机型与节点')
  })
})
