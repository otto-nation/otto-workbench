import assert from 'node:assert/strict';
import { test } from 'node:test';
import { remarkStripTitle } from './remark-strip-title.mjs';

function run(children) {
  const tree = { type: 'root', children };
  remarkStripTitle()(tree);
  return tree.children;
}

test('removes a leading h1', () => {
  const out = run([
    { type: 'heading', depth: 1, children: [] },
    { type: 'paragraph', children: [] },
  ]);
  assert.deepEqual(out.map((n) => n.type), ['paragraph']);
});

test('leaves an h2 alone', () => {
  const out = run([
    { type: 'heading', depth: 2, children: [] },
    { type: 'paragraph', children: [] },
  ]);
  assert.deepEqual(out.map((n) => n.type), ['heading', 'paragraph']);
});

test('leaves a later h1 alone once an h2 came first', () => {
  const out = run([
    { type: 'heading', depth: 2, children: [] },
    { type: 'heading', depth: 1, children: [] },
  ]);
  assert.equal(out.length, 2);
});

test('tolerates a document with no headings', () => {
  const out = run([{ type: 'paragraph', children: [] }]);
  assert.equal(out.length, 1);
});
