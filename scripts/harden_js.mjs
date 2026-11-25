#!/usr/bin/env node
/**
 * Stage 1 distribution hardener for NidoOS ESM/JS files.
 * Strips JS comments and redundant whitespace lexically while preserving
 * shebangs, strings, template literals (${...}), and regex literals.
 * Validates every output file with `node --check`.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const REGEX_PRECEDING_KEYWORDS = new Set([
  'return',
  'throw',
  'case',
  'delete',
  'void',
  'typeof',
  'yield',
  'await',
  'in',
  'instanceof',
  'of',
  'else',
  'do',
  'new',
  'export',
  'default',
]);

function canPrecedeRegex(prevTokenType, prevWord) {
  if (prevTokenType === 'start' || prevTokenType === 'punct' || prevTokenType === 'arrow') {
    return true;
  }
  if (prevTokenType === 'word' && REGEX_PRECEDING_KEYWORDS.has(prevWord)) {
    return true;
  }
  return false;
}

export function stripJsCommentsAndCompact(source) {
  let shebang = '';
  let startIdx = 0;
  if (source.startsWith('#!')) {
    const nl = source.indexOf('\n');
    if (nl !== -1) {
      shebang = source.slice(0, nl + 1);
      startIdx = nl + 1;
    } else {
      return source;
    }
  }

  let out = '';
  let i = startIdx;
  const len = source.length;
  let prevTokenType = 'start';
  let prevWord = '';
  const modeStack = []; // 'code' or 'template' or 'tpl-expr' (tracks brace depth)
  let exprBraceDepths = [];

  while (i < len) {
    // If returning to template literal after closing `}` of `${...}`
    if (modeStack.length > 0 && modeStack[modeStack.length - 1] === 'tpl') {
      const ch = source[i];
      if (ch === '\\' && i + 1 < len) {
        out += ch + source[i + 1];
        i += 2;
        continue;
      }
      if (ch === '`') {
        out += ch;
        modeStack.pop();
        prevTokenType = 'literal';
        prevWord = '';
        i += 1;
        continue;
      }
      if (ch === '$' && source[i + 1] === '{') {
        out += '${';
        modeStack.push('expr');
        exprBraceDepths.push(1);
        prevTokenType = 'punct';
        prevWord = '';
        i += 2;
        continue;
      }
      out += ch;
      i += 1;
      continue;
    }

    const ch = source[i];
    const next = source[i + 1] ?? '';

    // Line comment
    if (ch === '/' && next === '/') {
      i += 2;
      while (i < len && source[i] !== '\n') i += 1;
      continue;
    }

    // Block comment
    if (ch === '/' && next === '*') {
      i += 2;
      while (i < len && !(source[i] === '*' && source[i + 1] === '/')) i += 1;
      i += 2;
      out += ' ';
      continue;
    }

    // Strings
    if (ch === '"' || ch === "'") {
      const quote = ch;
      out += ch;
      i += 1;
      while (i < len) {
        const sc = source[i];
        out += sc;
        i += 1;
        if (sc === '\\' && i < len) {
          out += source[i];
          i += 1;
        } else if (sc === quote) {
          break;
        }
      }
      prevTokenType = 'literal';
      prevWord = '';
      continue;
    }

    // Template literal start
    if (ch === '`') {
      out += ch;
      modeStack.push('tpl');
      i += 1;
      continue;
    }

    // Regex literal vs division operator
    if (ch === '/' && canPrecedeRegex(prevTokenType, prevWord)) {
      out += '/';
      i += 1;
      let inClass = false;
      while (i < len) {
        const rc = source[i];
        out += rc;
        i += 1;
        if (rc === '\\' && i < len) {
          out += source[i];
          i += 1;
          continue;
        }
        if (rc === '[') {
          inClass = true;
        } else if (rc === ']') {
          inClass = false;
        } else if (rc === '/' && !inClass) {
          break;
        } else if (rc === '\n') {
          break;
        }
      }
      while (i < len && /[a-z]/i.test(source[i])) {
        out += source[i];
        i += 1;
      }
      prevTokenType = 'literal';
      prevWord = '';
      continue;
    }

    // Track braces inside `${...}` template expression
    if (modeStack.length > 0 && modeStack[modeStack.length - 1] === 'expr') {
      if (ch === '{') {
        exprBraceDepths[exprBraceDepths.length - 1] += 1;
      } else if (ch === '}') {
        exprBraceDepths[exprBraceDepths.length - 1] -= 1;
        if (exprBraceDepths[exprBraceDepths.length - 1] === 0) {
          out += '}';
          exprBraceDepths.pop();
          modeStack.pop();
          i += 1;
          continue;
        }
      }
    }

    // Arrow `=>`
    if (ch === '=' && next === '>') {
      out += '=>';
      prevTokenType = 'arrow';
      prevWord = '';
      i += 2;
      continue;
    }

    // `++` / `--`
    if ((ch === '+' && next === '+') || (ch === '-' && next === '-')) {
      out += ch + next;
      prevTokenType = 'postfix';
      prevWord = '';
      i += 2;
      continue;
    }

    // Identifiers / keywords
    if (/[A-Za-z_$]/.test(ch)) {
      let word = ch;
      i += 1;
      while (i < len && /[A-Za-z0-9_$]/.test(source[i])) {
        word += source[i];
        i += 1;
      }
      out += word;
      prevTokenType = 'word';
      prevWord = word;
      continue;
    }

    // Numbers
    if (/[0-9]/.test(ch)) {
      let num = ch;
      i += 1;
      while (i < len && /[0-9A-Za-z_.]/.test(source[i])) {
        num += source[i];
        i += 1;
      }
      out += num;
      prevTokenType = 'literal';
      prevWord = '';
      continue;
    }

    // Whitespace compaction
    if (ch === ' ' || ch === '\t' || ch === '\r') {
      if (out.length > 0 && out[out.length - 1] !== ' ' && out[out.length - 1] !== '\n') {
        out += ' ';
      }
      i += 1;
      continue;
    }
    if (ch === '\n') {
      if (out.endsWith(' ')) {
        out = out.slice(0, -1);
      }
      if (out.length > 0 && !out.endsWith('\n')) {
        out += '\n';
      }
      i += 1;
      continue;
    }

    out += ch;
    if (ch === ')' || ch === ']') {
      prevTokenType = 'close';
    } else {
      prevTokenType = 'punct';
    }
    prevWord = '';
    i += 1;
  }

  return `${shebang}${out.trim()}\n`;
}

export function hardenJsFile(filePath) {
  const raw = fs.readFileSync(filePath, 'utf8');
  const hardened = stripJsCommentsAndCompact(raw);
  fs.writeFileSync(filePath, hardened, 'utf8');
  execFileSync(process.execPath, ['--check', filePath], { stdio: 'pipe' });
}

function runSelfTest() {
  const sample = `#!/usr/bin/env node
// Header comment that must vanish
/* Multi-line
   secret comment */
export function run(x) {
  const url = "https://example.com/a//b";
  const cleaned = x.replace(/\\/+$/g, '');
  const half = 10 / 2; // inline division comment
  const msg = \`hi \${cleaned /* block comment */} // keep-inside-template \${half}\`;
  return msg + ':' + url;
}
`;
  const out = stripJsCommentsAndCompact(sample);
  if (!out.startsWith('#!/usr/bin/env node\n')) {
    throw new Error('Missing preserved shebang');
  }
  if (out.includes('Header comment') || out.includes('secret comment') || out.includes('inline division') || out.includes('block comment')) {
    throw new Error(`Comment leaked in hardened JS:\n${out}`);
  }
  if (!out.includes('https://example.com/a//b') || !out.includes('keep-inside-template')) {
    throw new Error(`String/template literal corrupted:\n${out}`);
  }
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'nido-harden-js-'));
  try {
    const tmpFile = path.join(tmpDir, 'test.mjs');
    fs.writeFileSync(tmpFile, out, 'utf8');
    execFileSync(process.execPath, ['--check', tmpFile], { stdio: 'pipe' });
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
  console.log('harden_js.mjs self-test passed.');
}

const args = process.argv.slice(2);
if (args.includes('--self-test')) {
  runSelfTest();
} else if (args.length === 0) {
  console.error('Usage: harden_js.mjs [--self-test] <file1.js|mjs> ...');
  process.exit(2);
} else {
  for (const target of args) {
    hardenJsFile(path.resolve(target));
  }
}
