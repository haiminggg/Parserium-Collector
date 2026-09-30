import { test } from 'node:test';
import assert from 'node:assert/strict';
import { allowed } from './access.mjs';

test('missing secret fails closed', () => {
  assert.equal(allowed(new Request('https://example.test/run', {method: 'POST'}), undefined), false);
});
test('wrong bearer is rejected', () => {
  assert.equal(allowed(new Request('https://example.test/run', {method: 'POST', headers: {Authorization: 'Bearer wrong'}}), 'a'.repeat(64)), false);
});
test('only authenticated POST /run without query is accepted', () => {
  const token = 'a'.repeat(64);
  const make = (path, method) => new Request(`https://example.test${path}`, {method, headers: {Authorization: `Bearer ${token}`}});
  assert.equal(allowed(make('/run', 'POST'), token), true);
  assert.equal(allowed(make('/run', 'GET'), token), false);
  assert.equal(allowed(make('/run?url=other', 'POST'), token), false);
  assert.equal(allowed(make('/other', 'POST'), token), false);
});
