import assert from 'node:assert/strict';
import { test } from 'node:test';
import { rewrite } from './remark-doc-links.mjs';

test('bare sibling doc link becomes a route', () => {
  assert.equal(rewrite('user-overrides.md'), '/docs/user-overrides');
});

test('dot-slash sibling doc link becomes a route', () => {
  assert.equal(rewrite('./user-overrides.md'), '/docs/user-overrides');
});

test('doc link keeps its fragment', () => {
  assert.equal(rewrite('libraries.md#rootssh'), '/docs/libraries#rootssh');
});

test('repo file link becomes a GitHub blob URL', () => {
  assert.equal(
    rewrite('../lib/files.sh'),
    'https://github.com/otto-nation/otto-workbench/blob/main/lib/files.sh',
  );
});

test('repo directory link becomes a GitHub tree URL', () => {
  assert.equal(
    rewrite('../git/hooks/'),
    'https://github.com/otto-nation/otto-workbench/tree/main/git/hooks/',
  );
});

test('repo markdown outside the collection becomes a GitHub URL, not a route', () => {
  assert.equal(
    rewrite('../CLAUDE.md#conventions'),
    'https://github.com/otto-nation/otto-workbench/blob/main/CLAUDE.md#conventions',
  );
});

test('external URLs are untouched', () => {
  assert.equal(rewrite('https://example.com/x.md'), 'https://example.com/x.md');
});

test('anchors are untouched', () => {
  assert.equal(rewrite('#adding-an-entry'), '#adding-an-entry');
});

test('absolute paths are untouched', () => {
  assert.equal(rewrite('/docs/architecture'), '/docs/architecture');
});

test('unrecognised relative forms are left alone rather than guessed at', () => {
  assert.equal(rewrite('../../outside.md'), '../../outside.md');
});
