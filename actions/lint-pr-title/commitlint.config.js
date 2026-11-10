// The organization's rules for pull request titles. lint-pr-title copies this
// file elsewhere before loading it, so it must stay self-contained: no
// relative imports. A repository adds its own types through the action's
// `extra-types` input, which arrives as LINT_PR_TITLE_EXTRA_TYPES.
const types = ['feat', 'fix', 'perf', 'docs', 'deps', 'ci', 'refactor', 'revert', 'test', 'chore'];

const extraTypes = (process.env.LINT_PR_TITLE_EXTRA_TYPES ?? '').split(/[\s,]+/).filter(Boolean);

export default {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'body-leading-blank': [1, 'always'],
    'body-max-line-length': [1, 'always', 100],
    'footer-leading-blank': [1, 'always'],
    'footer-max-line-length': [1, 'always', 100],
    'header-max-length': [1, 'always', 100],
    'subject-case': [0, 'never', ['sentence-case', 'start-case', 'pascal-case', 'upper-case']],
    'subject-empty': [2, 'never'],
    'subject-full-stop': [2, 'never', '.'],
    'type-case': [2, 'always', ['lower-case', 'camel-case']],
    'type-empty': [2, 'never'],
    'type-enum': [2, 'always', [...new Set([...types, ...extraTypes])]],
  },
};
