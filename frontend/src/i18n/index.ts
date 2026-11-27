import i18n from 'i18next'
import {initReactI18next} from 'react-i18next'
import enStrings from '../../locales/en/strings.json'
import zhStrings from '../../locales/zh/strings.json'

const resources = {
  en: {
    translation: enStrings,
  },
  zh: {
    translation: zhStrings,
  },
}

i18n.use(initReactI18next).init({
  resources,
  // The portal is used in Chinese; a key missing in zh falls back to English (i18nParity.test.ts keeps them in sync).
  lng: 'zh',
  fallbackLng: 'en',
  interpolation: {
    escapeValue: false, // react already safes from xss
  },
  react: {
    transKeepBasicHtmlNodesFor: ['br', 'strong', 'i', 'code', 'p'],
  },
})

export default i18n
