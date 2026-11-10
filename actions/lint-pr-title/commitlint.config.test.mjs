import assert from 'node:assert/strict';
import { test } from 'node:test';

const orgTypes = ['feat', 'fix', 'perf', 'docs', 'deps', 'ci', 'refactor', 'revert', 'test', 'chore'];

// The config reads the environment when it is imported, so each case imports a
// fresh copy.
async function allowedTypes(extraTypes) {
  if (extraTypes === undefined) {
    delete process.env.LINT_PR_TITLE_EXTRA_TYPES;
  } else {
    process.env.LINT_PR_TITLE_EXTRA_TYPES = extraTypes;
  }
  const url = new URL(`./commitlint.config.js?case=${Math.random()}`, import.meta.url);
  const { default: config } = await import(url);
  return config.rules['type-enum'][2];
}

test('accepts only the organization types without extra types', async () => {
  assert.deepEqual(await allowedTypes(undefined), orgTypes);
  assert.deepEqual(await allowedTypes(''), orgTypes);
});

test('adds extra types separated by commas, spaces or newlines', async () => {
  assert.deepEqual(await allowedTypes('build, style\n  engine\n\nrepo website'), [
    ...orgTypes,
    'build',
    'style',
    'engine',
    'repo',
    'website',
  ]);
});

test('lists a type once when an extra type repeats an organization type', async () => {
  assert.deepEqual(await allowedTypes('fix,build,build'), [...orgTypes, 'build']);
});
