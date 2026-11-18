// ESLint for the browser scripts shipped to OPNsense (no bundler, ES2020).
import globals from 'globals';

export default [
  {
    files: ['misc/theme-unisense/src/opnsense/www/**/*.js'],
    languageOptions: {
      ecmaVersion: 2020,
      sourceType: 'script',
      globals: { ...globals.browser, $: 'readonly', jQuery: 'readonly', BootstrapDialog: 'readonly' },
    },
    rules: {
      'no-undef': 'error',
      'no-redeclare': 'error',
      'no-use-before-define': ['error', { functions: false, variables: true }],
      'no-implicit-globals': 'error',
      'no-unused-vars': ['error', { args: 'none', caughtErrors: 'none' }],
      'no-shadow': 'error',
      'eqeqeq': 'error',
      'no-unreachable': 'error',
      'no-dupe-keys': 'error',
    },
  },
];
