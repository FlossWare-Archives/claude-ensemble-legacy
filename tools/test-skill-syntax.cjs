const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const skillFiles = [
  'hooks/example-workflow-with-learning.js',
  'hooks/memory-rag-search.js',
  'tools/code-doc-auto.js',
  'tools/code-pr-review-auto.js',
  'tools/code-release-notes-auto.js',
  'tools/code-release-notes.js',
];

for (const file of skillFiles) {
  let source = fs.readFileSync(file, 'utf8');

  // These are host-executed workflow skills, not standalone Node modules.
  // Their runtime supplies args/log/phase/agent/workflow and permits top-level
  // return/await. Strip static module declarations and exports for syntax-only
  // validation inside the async function shape used by the skill host.
  source = source.replace(/^\s*import\s+.*?from\s+['"][^'"]+['"];?\s*$/gm, '');
  source = source.replace(/^\s*export\s+(?=(?:const|let|var|async\s+function|function|class)\b)/gm, '');

  const wrapped = `(async function __workflow_skill__(args, log, phase, agent, workflow, require) {\n${source}\n})`;
  new vm.Script(wrapped, { filename: path.resolve(file) });
  process.stdout.write(`PASS workflow-skill syntax: ${file}\n`);
}
