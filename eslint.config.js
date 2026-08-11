import importX from 'eslint-plugin-import-x';

export default [{
  files: ['screen.js', 'src/**/*.js'],
  languageOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
    globals: {
      AbortController: 'readonly',
      Blob: 'readonly',
      console: 'readonly',
      document: 'readonly',
      DOMException: 'readonly',
      fetch: 'readonly',
      localStorage: 'readonly',
      queueMicrotask: 'readonly',
      setTimeout: 'readonly',
      clearTimeout: 'readonly',
      URL: 'readonly',
      URLSearchParams: 'readonly',
      window: 'readonly',
    },
  },
  plugins: { 'import-x': importX },
  rules: {
    'import-x/no-cycle': 'error',
    'import-x/no-unresolved': 'error',
    'max-lines': ['error', { max: 1500, skipBlankLines: true, skipComments: true }],
    'no-undef': 'error',
    'no-unused-vars': ['error', { argsIgnorePattern: '^_', caughtErrors: 'none' }],
  },
}];
