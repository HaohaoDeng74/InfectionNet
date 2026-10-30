const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

function frontend() {
  const elements = new Map();
  function el(id) {
    if (!elements.has(id)) elements.set(id, { value: id === 'sort' ? 'new' : '', textContent: '', innerHTML: '', addEventListener() {}, querySelectorAll() { return []; } });
    return elements.get(id);
  }
  const document = { getElementById: el, querySelectorAll() { return []; }, querySelector() { return el('focus'); }, addEventListener() {} };
  const context = vm.createContext({ document, Date, setInterval() {}, fetch: () => new Promise(() => {}) });
  const html = fs.readFileSync('index.html', 'utf8');
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
  return { run: code => vm.runInContext(code, context), el, context };
}
const now = Date.parse('2026-10-30T23:59:59Z');

test('stale week labels are removed without mutating the snapshot', () => {
  const f = frontend();
  const result = f.run(`const p={firstPublicationDate:'2026-10-01',topics:['week','sepsis','week']};JSON.stringify([topicList(p,${now}),p.topics])`);
  assert.deepEqual(JSON.parse(result), [['sepsis'], ['week', 'sepsis', 'week']]);
});
test('seven UTC calendar days include today through day minus six only', () => {
  const f = frontend();
  for (const [date, expected] of [['2026-10-30',true],['2026-10-24',true],['2026-10-23',false],['2026-10-31',false]]) {
    assert.equal(f.run(`weekOld({firstPublicationDate:'${date}'},${now})`), expected, date);
  }
  assert.equal(f.run(`weekOld({firstPublicationDate:'2026-10-24'},${Date.parse('2026-10-31T00:00:00Z')})`), false);
});
test('invalid, missing, partial, future and fallback dates cannot acquire week', () => {
  const f = frontend();
  for (const value of ['',null,'2026','2026-02-30','not-a-date','2026-10-30T01:00:00Z']) {
    assert.equal(f.run(`weekOld({firstPublicationDate:${JSON.stringify(value)},pubDate:'2026-10-30',pubYear:'2026'},${now})`), false);
  }
});
test('recent records receive exactly one week tag from every topic', () => {
  const f = frontend();
  for (const topic of ['sepsis', 'microbial_single_cell', 'infection_omics']) {
    assert.deepEqual(JSON.parse(f.run(`JSON.stringify(topicList({firstPublicationDate:'2026-10-30',topics:['week','${topic}','week']},${now}))`)), [topic, 'week']);
  }
});
test('actual render excludes stale snapshots and day refresh expires an open tab', () => {
  const f = frontend();
  f.context.Date = class extends Date { static now() { return now; } };
  f.run(`papers=[{id:'expired',title:'Expired',topics:['week'],firstPublicationDate:'2026-10-23'},{id:'fresh',title:'Fresh',topics:['sepsis'],firstPublicationDate:'2026-10-24'}];topic='week';render();`);
  assert.match(f.el('papers').innerHTML, /Fresh/);
  assert.doesNotMatch(f.el('papers').innerHTML, /Expired/);
  f.run(`renderedDay=Math.floor(Date.now()/86400000)`);
  f.context.Date = class extends Date { static now() { return Date.parse('2026-10-31T00:00:00Z'); } };
  f.run('refreshDay()');
  assert.match(f.el('papers').innerHTML, /没有找到匹配/);
});
