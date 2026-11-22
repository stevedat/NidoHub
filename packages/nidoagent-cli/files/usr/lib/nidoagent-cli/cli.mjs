#!/usr/bin/env node
/**
 * NidoAgent CLI (NidoOS)
 * Zero-dependency ESM client talking to the local NidoAI Gateway.
 */
import { execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';

const VERSION = '0.2.0';
const MAX_ATTACH_BYTES = 65_536;
const OFFICE_EXTS = new Set([
  '.docx',
  '.xlsx',
  '.pptx',
  '.doc',
  '.xls',
  '.ppt',
  '.odt',
  '.ods',
  '.odp',
  '.pdf',
  '.hwp',
  '.hwpx',
]);

function stateDirCandidates() {
  const dirs = [];
  if (process.env.NIDOAI_HOME) {
    dirs.push(process.env.NIDOAI_HOME);
  }
  dirs.push(path.join(os.homedir(), '.nidoai'));
  return [...new Set(dirs)];
}

function parseEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return {};
  const out = {};
  for (const raw of fs.readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq > 0) {
      out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim().replace(/^["']|["']$/g, '');
    }
  }
  return out;
}

function resolveGatewayConnection() {
  let baseUrl = process.env.NIDOAI_BASE_URL || '';
  let clientToken = process.env.NIDOAI_CLIENT_TOKEN || '';
  let runtimeFound = null;

  for (const dir of stateDirCandidates()) {
    const rtPath = path.join(dir, 'runtime.json');
    if (fs.existsSync(rtPath)) {
      try {
        const rt = JSON.parse(fs.readFileSync(rtPath, 'utf8'));
        runtimeFound = rtPath;
        if (!baseUrl && typeof rt.baseUrl === 'string') baseUrl = rt.baseUrl;
        if (!clientToken && typeof rt.clientToken === 'string') clientToken = rt.clientToken;
      } catch {
        // ignore malformed runtime file
      }
    }
    const envLocal = parseEnvFile(path.join(dir, '.env.local'));
    if (!clientToken && envLocal.NIDOAI_CLIENT_TOKEN) {
      clientToken = envLocal.NIDOAI_CLIENT_TOKEN;
    }
    if (!baseUrl && envLocal.NIDOAI_PORT) {
      baseUrl = `http://127.0.0.1:${envLocal.NIDOAI_PORT}`;
    }
  }

  if (!baseUrl) {
    const port = process.env.NIDOAI_GATEWAY_PORT || process.env.NIDOAI_PORT || '18090';
    baseUrl = `http://127.0.0.1:${port}`;
  }

  return {
    baseUrl: baseUrl.replace(/\/+$/, ''),
    clientToken,
    runtimeFound,
  };
}

function printHelp() {
  console.log(`NidoAgent CLI v${VERSION} (NidoOS)

Usage:
  nidoagent status | doctor                                            Kiểm tra kết nối NidoAI Gateway, Agent CLI & năng lực hệ thống
  nidoagent account [status|login|logout|sync] [--json]                Quản lý Tài khoản Nido (SSO, trạng thái gói & đồng bộ quota)
  nidoagent capabilities [--json]                                      Liệt kê công cụ bản địa & OS Skills sẵn sàng cho AI Agent
  nidoagent skills [list|sync] [--all] [--json]                        Liệt kê hoặc đồng bộ OS Skills cho agy / codex / claude
  nidoagent ask [--model <id>] [--effort <lvl>] [--json|--plain|--raw] [--file <p>] [--out <p>] <prompt>
                                                                       Gửi câu hỏi / tệp (streaming nhanh) & xuất báo cáo
  nidoagent chat [--model <id>] [--effort <lvl>] [--plain|--raw]       Mở phiên hội thoại giữ ấm (warm session)
  nidoagent --version | --help                                         Hiện thông tin phiên bản / trợ giúp
`);
}

const SKILL_NAME_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

function whichBinary(name) {
  if (!name) return null;
  if (name.includes(path.sep)) {
    try {
      fs.accessSync(name, fs.constants.X_OK);
      return fs.statSync(name).isFile() ? path.resolve(name) : null;
    } catch {
      return null;
    }
  }
  const pathEnv = process.env.PATH || '';
  const extraDirs = [
    path.join(os.homedir(), '.local', 'bin'),
    '/usr/local/bin',
    '/usr/bin',
    '/bin',
  ];
  const dirs = [...new Set([...pathEnv.split(path.delimiter).filter(Boolean), ...extraDirs])];
  for (const dir of dirs) {
    const candidate = path.join(dir, name);
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      if (fs.statSync(candidate).isFile()) return candidate;
    } catch {
      // continue
    }
  }
  return null;
}

function hasTesseractVie() {
  const candidates = [
    '/usr/share/tesseract-ocr/5/tessdata/vie.traineddata',
    '/usr/share/tesseract-ocr/4.00/tessdata/vie.traineddata',
    '/usr/share/tessdata/vie.traineddata',
    '/opt/homebrew/share/tessdata/vie.traineddata',
  ];
  return candidates.some((p) => fs.existsSync(p));
}

function resolveOsSkillsDir() {
  const override = (process.env.NIDO_SKILLS_DIR || '').trim();
  if (override) return path.resolve(override);
  return '/usr/share/ubuntu-nido/skills';
}

function parseSkillFrontmatter(raw) {
  const meta = { name: '', description: '' };
  const match = /^---\r?\n([\s\S]*?)\r?\n---/.exec(raw);
  if (!match) return meta;
  const lines = match[1].split(/\r?\n/);
  let currentKey = '';
  for (const line of lines) {
    const kv = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
    if (kv) {
      currentKey = kv[1].trim();
      const val = kv[2].trim();
      if (val && val !== '>' && val !== '>-' && val !== '|' && val !== '|-') {
        meta[currentKey] = val.replace(/^["']|["']$/g, '');
      }
    } else if (currentKey === 'description' && /^\s+/.test(line)) {
      const part = line.trim();
      if (part) {
        meta.description = meta.description ? `${meta.description} ${part}` : part;
      }
    }
  }
  return meta;
}

function listOsSkills() {
  const skillsDir = resolveOsSkillsDir();
  if (!fs.existsSync(skillsDir)) return { dir: skillsDir, skills: [] };
  let entries = [];
  try {
    entries = fs.readdirSync(skillsDir, { withFileTypes: true });
  } catch {
    return { dir: skillsDir, skills: [] };
  }
  const skills = [];
  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    if (!SKILL_NAME_RE.test(entry.name)) continue;
    if (!entry.isDirectory() && !entry.isSymbolicLink()) continue;
    const dir = path.join(skillsDir, entry.name);
    const skillFile = path.join(dir, 'SKILL.md');
    if (!fs.existsSync(skillFile)) continue;
    let front = { name: '', description: '' };
    try {
      front = parseSkillFrontmatter(fs.readFileSync(skillFile, 'utf8'));
    } catch {
      // ignore unreadable frontmatter
    }
    skills.push({
      name: front.name || entry.name,
      dirName: entry.name,
      description: front.description || '',
      dir,
      skillFile,
    });
  }
  return { dir: skillsDir, skills };
}

function syncOsSkills(options = {}) {
  const syncAll = Boolean(options.syncAll);
  const home = os.homedir();
  const { dir: skillsDir, skills } = listOsSkills();
  const resolvedSkillsRoot = fs.existsSync(skillsDir) ? fs.realpathSync(skillsDir) : path.resolve(skillsDir);

  const targets = [
    {
      id: 'agents',
      parentDir: path.join(home, '.agents'),
      skillsDir: path.join(home, '.agents', 'skills'),
      always: true,
    },
    {
      id: 'agy',
      parentDir: path.join(home, '.gemini', 'config'),
      skillsDir: path.join(home, '.gemini', 'config', 'skills'),
      bin: 'agy',
    },
    {
      id: 'claude',
      parentDir: path.join(home, '.claude'),
      skillsDir: path.join(home, '.claude', 'skills'),
      bin: 'claude',
    },
    {
      id: 'codex',
      parentDir: path.join(home, '.codex'),
      skillsDir: path.join(home, '.codex', 'skills'),
      bin: 'codex',
    },
  ];

  const results = [];
  for (const target of targets) {
    const active =
      syncAll ||
      Boolean(target.always) ||
      fs.existsSync(target.parentDir) ||
      Boolean(target.bin && whichBinary(target.bin));
    if (!active) {
      results.push({ target: target.id, dir: target.skillsDir, active: false, items: [] });
      continue;
    }
    const items = [];
    if (skills.length > 0) {
      fs.mkdirSync(target.skillsDir, { recursive: true });
    }
    for (const skill of skills) {
      const dest = path.join(target.skillsDir, skill.dirName);
      const expectedTarget = path.resolve(skill.dir);
      let stat = null;
      try {
        stat = fs.lstatSync(dest);
      } catch {
        stat = null;
      }
      if (!stat) {
        fs.symlinkSync(expectedTarget, dest);
        items.push({ skill: skill.dirName, status: 'linked', path: dest });
        continue;
      }
      if (!stat.isSymbolicLink()) {
        items.push({ skill: skill.dirName, status: 'skipped_user_override', path: dest });
        continue;
      }
      const rawLink = fs.readlinkSync(dest);
      const resolvedLink = path.resolve(path.dirname(dest), rawLink);
      if (resolvedLink === expectedTarget) {
        items.push({ skill: skill.dirName, status: 'unchanged', path: dest });
        continue;
      }
      if (
        resolvedLink === resolvedSkillsRoot ||
        resolvedLink.startsWith(`${resolvedSkillsRoot}${path.sep}`)
      ) {
        fs.unlinkSync(dest);
        fs.symlinkSync(expectedTarget, dest);
        items.push({ skill: skill.dirName, status: 'updated', path: dest });
      } else {
        items.push({ skill: skill.dirName, status: 'skipped_user_override', path: dest });
      }
    }
    results.push({ target: target.id, dir: target.skillsDir, active: true, items });
  }

  return {
    ok: true,
    sourceDir: skillsDir,
    skillsCount: skills.length,
    targets: results,
  };
}

function sanitizeNidoAccountObject(raw) {
  if (!raw || typeof raw !== 'object' || raw.signedIn !== true) {
    return {
      signedIn: false,
      planCode: 'free',
      planName: 'Miễn phí (Free)',
      isPro: false,
      source: 'nido',
    };
  }
  const out = {
    signedIn: true,
    userId: typeof raw.userId === 'string' ? raw.userId : '',
    email: typeof raw.email === 'string' ? raw.email : '',
    fullName: typeof raw.fullName === 'string' ? raw.fullName : '',
    planCode: typeof raw.planCode === 'string' && raw.planCode ? raw.planCode : 'free',
    planName:
      typeof raw.planName === 'string' && raw.planName
        ? raw.planName
        : raw.isPro
          ? 'Gói Đồng Hành (Pro)'
          : 'Miễn phí (Free)',
    isPro: Boolean(raw.isPro),
    source: typeof raw.source === 'string' && raw.source ? raw.source : 'nido',
    updatedAt: typeof raw.updatedAt === 'string' ? raw.updatedAt : '',
  };
  if (raw.quota && typeof raw.quota === 'object') {
    out.quota = { ...raw.quota };
  }
  if (raw.sync && typeof raw.sync === 'object') {
    out.sync = {
      enabled: Boolean(raw.sync.enabled),
      lastSyncAt: typeof raw.sync.lastSyncAt === 'string' ? raw.sync.lastSyncAt : '',
      items: Array.isArray(raw.sync.items) ? raw.sync.items.map((x) => String(x)) : [],
    };
  }
  return out;
}

function readSanitizedNidoAccount() {
  const xdgConfig = (process.env.XDG_CONFIG_HOME || '').trim() || path.join(os.homedir(), '.config');
  const accountPath = path.join(xdgConfig, 'ubuntu-nido', 'account.json');
  if (!fs.existsSync(accountPath)) {
    return sanitizeNidoAccountObject(null);
  }
  try {
    const parsed = JSON.parse(fs.readFileSync(accountPath, 'utf8'));
    return sanitizeNidoAccountObject(parsed);
  } catch {
    return sanitizeNidoAccountObject(null);
  }
}

function collectCapabilities() {
  const officeCliPath = whichBinary(process.env.NIDOOFFICE_BIN || 'nidooffice');
  const nidoboxPath = whichBinary('nidobox');
  const tesseractPath = whichBinary('tesseract');
  const signedInMap = new Map(readSignedInAgentClis().map((a) => [a.id, a]));
  const agents = {};
  for (const id of ['agy', 'codex', 'claude']) {
    const signed = signedInMap.get(id);
    const binPath = signed?.binaryPath || whichBinary(id);
    agents[id] = {
      installed: Boolean(binPath),
      signedIn: Boolean(signed),
      ...(binPath ? { binaryPath: binPath } : {}),
    };
  }
  const nidoAccount = readSanitizedNidoAccount();
  const { dir: skillsDir, skills } = listOsSkills();
  return {
    office: {
      cli: Boolean(officeCliPath),
      ...(officeCliPath ? { cliPath: officeCliPath } : {}),
      desktop:
        fs.existsSync('/opt/NidoOffice/genoffice') ||
        fs.existsSync('/opt/NidoOffice/nidooffice') ||
        Boolean(whichBinary('nidooffice-desktop')),
    },
    utilities: {
      nidobox: Boolean(nidoboxPath),
      ...(nidoboxPath ? { nidoboxPath } : {}),
      nidoboxEngine: fs.existsSync('/usr/lib/nidobox/nidobox-engine'),
      tesseract: Boolean(tesseractPath),
      tesseractVie: hasTesseractVie(),
    },
    input: {
      ibus: Boolean(whichBinary('ibus')),
      vielotus:
        fs.existsSync('/usr/libexec/ibus-engine-vielotus') ||
        fs.existsSync('/usr/lib/ibus-vielotus') ||
        Boolean(whichBinary('vielotus-setup')),
    },
    email: {
      geary: Boolean(whichBinary('geary')),
      himalaya: Boolean(whichBinary('himalaya')),
    },
    nidoAccount: {
      signedIn: nidoAccount.signedIn,
      ...(nidoAccount.email ? { email: nidoAccount.email } : {}),
      planCode: nidoAccount.planCode,
      planName: nidoAccount.planName,
      isPro: nidoAccount.isPro,
    },
    agents,
    multimedia: {
      ffmpeg: Boolean(whichBinary('ffmpeg')),
      imagemagick: Boolean(whichBinary('magick') || whichBinary('convert')),
      pandoc: Boolean(whichBinary('pandoc')),
      inkscape: Boolean(whichBinary('inkscape')),
    },
    skills: {
      dir: skillsDir,
      count: skills.length,
      available: skills.map((s) => ({ name: s.name, skillFile: s.skillFile })),
    },
  };
}

const INLINE_MD_TOKEN_RE =
  /`([^`\n]+)`|\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)|\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*|\*\*(?=\S)(.+?)(?<=\S)\*\*|(?<![\p{L}\p{N}_])__(?=\S)(.+?)(?<=\S)__(?![\p{L}\p{N}_])|(?<!\*)\*(?=\S)(?!\*)([^*\n]+?)(?<=\S)\*(?!\*)|~~(?=\S)(.+?)(?<=\S)~~/gu;

function formatMarkdownInline(text, mode) {
  if (!text || mode === 'raw') return text;
  const isAnsi = mode === 'ansi';
  return text.replace(
    INLINE_MD_TOKEN_RE,
    (_m, code, linkLabel, _linkUrl, boldItalic, bold, boldU, italic, strike) => {
      if (code !== undefined) return isAnsi ? `\x1b[36m${code}\x1b[39m` : code;
      if (linkLabel !== undefined) return isAnsi ? `\x1b[4m${linkLabel}\x1b[24m` : linkLabel;
      if (boldItalic !== undefined) {
        const inner = boldItalic.replace(/`([^`\n]+)`/g, '$1');
        return isAnsi ? `\x1b[1m\x1b[3m${inner}\x1b[23m\x1b[22m` : inner;
      }
      if (bold !== undefined || boldU !== undefined) {
        const rawInner = bold !== undefined ? bold : boldU;
        const rendered = rawInner.includes('`')
          ? formatMarkdownInline(rawInner, mode)
          : rawInner;
        return isAnsi ? `\x1b[1m${rendered}\x1b[22m` : rendered;
      }
      if (italic !== undefined) return isAnsi ? `\x1b[3m${italic}\x1b[23m` : italic;
      if (strike !== undefined) return isAnsi ? `\x1b[9m${strike}\x1b[29m` : strike;
      return _m;
    }
  );
}

const BULLET_GLYPHS = ['•', '◦', '▪'];

function formatMarkdownLine(line, mode, state) {
  if (mode === 'raw') return line;
  const trimmed = line.trim();
  if (trimmed.startsWith('```')) {
    state.inCode = !state.inCode;
    return mode === 'ansi' ? `\x1b[2m${line}\x1b[22m` : '';
  }
  if (state.inCode) {
    return mode === 'ansi' ? `\x1b[36m${line}\x1b[39m` : line;
  }
  const hMatch = /^(#{1,4})\s+(.*)$/.exec(trimmed);
  if (hMatch) {
    const txt = formatMarkdownInline(hMatch[2].trim(), mode);
    return mode === 'ansi' ? `\x1b[1m\x1b[97m${txt}\x1b[39m\x1b[22m` : txt;
  }
  const bMatch = /^([ \t]*)[*\-+]\s+(.*)$/.exec(line);
  if (bMatch) {
    const spaces = bMatch[1].replace(/\t/g, '  ').length;
    const lvl = Math.min(Math.floor(spaces / 2), 2);
    const pad = '  '.repeat(lvl);
    const glyph = BULLET_GLYPHS[lvl];
    return `${pad}${glyph} ${formatMarkdownInline(bMatch[2].trim(), mode)}`;
  }
  if (/^\s*>\s?/.test(line)) {
    const q = line.replace(/^\s*>\s?/, '');
    const inner = formatMarkdownInline(q, mode);
    return mode === 'ansi' ? `\x1b[2m│\x1b[22m \x1b[3m${inner}\x1b[23m` : `│ ${inner}`;
  }
  return formatMarkdownInline(line, mode);
}

class MarkdownStreamWriter {
  constructor(mode, writeFn) {
    this.mode = mode;
    this.writeFn = writeFn;
    this.buf = '';
    this.state = { inCode: false };
    this.wroteAny = false;
    this.endedWithNewline = false;
  }

  push(chunk) {
    if (!chunk) return;
    if (this.mode === 'raw') {
      this.writeFn(chunk);
      this.wroteAny = true;
      this.endedWithNewline = chunk.endsWith('\n');
      return;
    }
    this.buf += chunk;
    let idx;
    while ((idx = this.buf.indexOf('\n')) !== -1) {
      const rawLine = this.buf.slice(0, idx);
      this.buf = this.buf.slice(idx + 1);
      const out = formatMarkdownLine(rawLine, this.mode, this.state);
      this.writeFn(`${out}\n`);
      this.wroteAny = true;
      this.endedWithNewline = true;
    }
  }

  flush() {
    if (this.mode !== 'raw' && this.buf.length > 0) {
      const out = formatMarkdownLine(this.buf, this.mode, this.state);
      this.buf = '';
      this.writeFn(`${out}\n`);
      this.wroteAny = true;
      this.endedWithNewline = true;
    } else if (this.wroteAny && !this.endedWithNewline) {
      this.writeFn('\n');
      this.endedWithNewline = true;
    }
  }
}

function renderMarkdownFull(text, mode) {
  if (mode === 'raw') return text;
  const state = { inCode: false };
  return text
    .split(/\r?\n/)
    .map((ln) => formatMarkdownLine(ln, mode, state))
    .join('\n');
}

function resolveOutputMode(flagPlain, flagRaw) {
  if (flagRaw) return 'raw';
  if (flagPlain) return 'plain';
  return process.stdout.isTTY ? 'ansi' : 'raw';
}

function readSignedInAgentClis() {
  const ready = [];
  for (const dir of stateDirCandidates()) {
    const statusPath = path.join(dir, 'agents-status.json');
    if (!fs.existsSync(statusPath)) continue;
    try {
      const parsed = JSON.parse(fs.readFileSync(statusPath, 'utf8'));
      const providers = parsed && typeof parsed === 'object' ? parsed.providers : null;
      if (!providers || typeof providers !== 'object') continue;
      for (const id of ['agy', 'codex', 'claude']) {
        const entry = providers[id];
        if (!entry || entry.signedIn !== true) continue;
        const bin = typeof entry.binaryPath === 'string' ? entry.binaryPath.trim() : '';
        if (bin && fs.existsSync(bin)) {
          ready.push({ id, binaryPath: bin, accountLabel: entry.accountLabel || '' });
        }
      }
      if (ready.length > 0) break;
    } catch {
      // ignore malformed status file
    }
  }
  return ready;
}

function buildAgyChildEnv() {
  const env = { ...process.env };
  if (process.platform === 'linux' && typeof process.getuid === 'function') {
    const uid = process.getuid();
    const busPath = `/run/user/${uid}/bus`;
    if (fs.existsSync(busPath)) {
      if (!env.XDG_RUNTIME_DIR) env.XDG_RUNTIME_DIR = `/run/user/${uid}`;
      if (!env.DBUS_SESSION_BUS_ADDRESS) env.DBUS_SESSION_BUS_ADDRESS = `unix:path=${busPath}`;
      delete env.SSH_CONNECTION;
      delete env.SSH_CLIENT;
      delete env.SSH_TTY;
    }
  }
  return env;
}

function ensureAgyEmptyWorkspace() {
  const wsDir = path.join(os.homedir(), '.cache', 'nidoagent-agy-ws');
  try {
    fs.mkdirSync(wsDir, { recursive: true, mode: 0o700 });
    return wsDir;
  } catch {
    return os.tmpdir();
  }
}

function buildAgyStreamArgs(options = {}) {
  const requestedModel = (options.model || process.env.NIDO_AGY_MODEL || process.env.NIDO_MODEL || '').trim();
  const useDefaultFast = !requestedModel || requestedModel === 'auto';
  const model = options.skipModelOverride ? '' : (useDefaultFast ? 'gemini-3.8-flash-low' : requestedModel);
  const effort = options.skipModelOverride
    ? ''
    : (options.effort || process.env.NIDO_AGY_EFFORT || (useDefaultFast ? 'low' : '')).trim();
  const args = [];
  if (model) args.push('--model', model);
  if (effort) args.push('--effort', effort);
  args.push(
    '--input-format',
    'stream-json',
    '--output-format',
    'stream-json',
    '--disable-slash-commands',
    '--print-timeout',
    '90s'
  );
  return args;
}

function executeAgyStreamOnce(agent, promptText, options = {}) {
  const runAttempt = (skipModelOverride) =>
    new Promise((resolve, reject) => {
      const args = buildAgyStreamArgs({ ...options, skipModelOverride });
      const child = spawn(agent.binaryPath, args, {
        cwd: ensureAgyEmptyWorkspace(),
        env: buildAgyChildEnv(),
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      let stdoutBuf = '';
      let stderrBuf = '';
      let streamedText = '';
      let finalResponse = '';
      let sawJsonEvent = false;
      let plainLines = [];
      let errorDetail = '';
      let settled = false;

      const timer = setTimeout(() => {
        if (!settled) {
          settled = true;
          try {
            child.kill('SIGTERM');
          } catch {
            // ignore
          }
          reject(new Error('Antigravity CLI (agy) quá thời gian phản hồi (95s).'));
        }
      }, 95_000);

      const handleLine = (line) => {
        const trimmed = line.trim();
        if (!trimmed) return;
        let parsed = null;
        if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
          try {
            parsed = JSON.parse(trimmed);
          } catch {
            parsed = null;
          }
        }
        if (parsed && typeof parsed === 'object' && typeof parsed.event === 'string') {
          sawJsonEvent = true;
          if (
            parsed.event === 'step_update' &&
            parsed.step_update &&
            parsed.step_update.step_type === 'agent_response' &&
            typeof parsed.step_update.text_delta === 'string'
          ) {
            const delta = parsed.step_update.text_delta;
            streamedText += delta;
            if (typeof options.onDelta === 'function') {
              options.onDelta(delta);
            }
          } else if (parsed.event === 'result' && parsed.result) {
            if (parsed.result.status === 'ERROR') {
              errorDetail = String(parsed.result.error || parsed.result.response || 'Lỗi agy').trim();
            } else if (typeof parsed.result.response === 'string') {
              finalResponse = parsed.result.response;
            }
          }
          return;
        }
        plainLines.push(line);
      };

      child.stdout.setEncoding('utf8');
      child.stdout.on('data', (chunk) => {
        stdoutBuf += chunk;
        let idx;
        while ((idx = stdoutBuf.indexOf('\n')) !== -1) {
          handleLine(stdoutBuf.slice(0, idx));
          stdoutBuf = stdoutBuf.slice(idx + 1);
        }
      });

      child.stderr.setEncoding('utf8');
      child.stderr.on('data', (chunk) => {
        stderrBuf += chunk;
      });

      child.on('error', (err) => {
        clearTimeout(timer);
        if (!settled) {
          settled = true;
          reject(err);
        }
      });

      child.on('close', (code) => {
        clearTimeout(timer);
        if (stdoutBuf.trim()) handleLine(stdoutBuf);
        if (settled) return;
        settled = true;
        const reply = (finalResponse || streamedText || (!sawJsonEvent ? plainLines.join('\n') : '')).trim();
        if (code === 0 && reply && !errorDetail) {
          resolve({ reply, streamed: Boolean(options.onDelta && streamedText.length > 0) });
          return;
        }
        const detail = errorDetail || stderrBuf.trim() || plainLines.join('\n').trim() || `exit ${code}`;
        const err = new Error(`Antigravity CLI (agy) lỗi: ${detail.slice(0, 260)}`);
        err.agyDetail = detail;
        reject(err);
      });

      try {
        child.stdin.write(`${JSON.stringify({ event: 'user', message: { content: promptText } })}\n`);
        child.stdin.end();
      } catch {
        // child closed stdin early
      }
    });

  return runAttempt(false).catch((err) => {
    const detail = String(err?.agyDetail || err?.message || '');
    if (!options.model && /model|unknown|invalid|unavailable|not found/i.test(detail)) {
      return runAttempt(true);
    }
    throw err;
  });
}

class AgyWarmSession {
  constructor(agent, options = {}) {
    this.agent = agent;
    this.options = options;
    this.child = null;
    this.buf = '';
    this.pending = null;
  }

  ensureStarted() {
    if (this.child && !this.child.killed && this.child.exitCode === null) {
      return;
    }
    const args = buildAgyStreamArgs(this.options);
    this.child = spawn(this.agent.binaryPath, args, {
      cwd: ensureAgyEmptyWorkspace(),
      env: buildAgyChildEnv(),
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    this.buf = '';
    this.child.stdout.setEncoding('utf8');
    this.child.stdout.on('data', (chunk) => {
      this.buf += chunk;
      let idx;
      while ((idx = this.buf.indexOf('\n')) !== -1) {
        this._handleLine(this.buf.slice(0, idx));
        this.buf = this.buf.slice(idx + 1);
      }
    });
    this.child.on('close', (code) => {
      if (this.pending) {
        const p = this.pending;
        this.pending = null;
        clearTimeout(p.timer);
        const partial = p.streamedText.trim();
        if (partial && code === 0) {
          p.resolve({ reply: partial, streamed: p.streamed });
        } else {
          p.reject(new Error(`Phiên giữ ấm agy đã đóng đột ngột (mã ${code}).`));
        }
      }
      this.child = null;
    });
  }

  _handleLine(line) {
    const trimmed = line.trim();
    if (!trimmed || !this.pending) return;
    let parsed;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      return;
    }
    if (
      parsed.event === 'step_update' &&
      parsed.step_update &&
      parsed.step_update.step_type === 'agent_response' &&
      typeof parsed.step_update.text_delta === 'string'
    ) {
      const delta = parsed.step_update.text_delta;
      this.pending.streamedText += delta;
      if (typeof this.pending.onDelta === 'function') {
        this.pending.streamed = true;
        this.pending.onDelta(delta);
      }
    } else if (parsed.event === 'result' && parsed.result) {
      const p = this.pending;
      this.pending = null;
      clearTimeout(p.timer);
      if (parsed.result.status === 'ERROR') {
        p.reject(new Error(String(parsed.result.error || 'Lỗi phản hồi từ agy')));
      } else {
        const reply = String(parsed.result.response || p.streamedText || '').trim();
        p.resolve({ reply, streamed: p.streamed });
      }
    }
  }

  sendTurn(content, onDelta) {
    this.ensureStarted();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.pending) {
          this.pending = null;
          this.close();
          reject(new Error('Phiên agy quá thời gian phản hồi (95s).'));
        }
      }, 95_000);
      this.pending = { resolve, reject, onDelta, streamedText: '', streamed: false, timer };
      try {
        this.child.stdin.write(`${JSON.stringify({ event: 'user', message: { content } })}\n`);
      } catch (err) {
        clearTimeout(timer);
        this.pending = null;
        reject(err);
      }
    });
  }

  close() {
    if (this.child) {
      try {
        this.child.stdin.end();
      } catch {
        // ignore
      }
      try {
        this.child.kill('SIGTERM');
      } catch {
        // ignore
      }
      this.child = null;
    }
  }
}

async function executeViaOfficialAgentCli(agent, messages, options = {}) {
  const promptText = messages
    .map((m) => (m.role === 'system' ? `[System]\n${m.content}` : m.content))
    .join('\n\n');

  if (agent.id === 'codex') {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'nidoagent-codex-'));
    const outFile = path.join(tmpDir, 'last-message.txt');
    try {
      execFileSync(
        agent.binaryPath,
        [
          'exec',
          '--skip-git-repo-check',
          '--ephemeral',
          '--color',
          'never',
          '-s',
          'read-only',
          '-o',
          outFile,
          '-',
        ],
        {
          input: promptText,
          encoding: 'utf8',
          maxBuffer: 4 * 1024 * 1024,
          timeout: 120_000,
          stdio: ['pipe', 'pipe', 'pipe'],
        }
      );
      if (fs.existsSync(outFile)) {
        const reply = fs.readFileSync(outFile, 'utf8').trim();
        if (reply) return { reply, streamed: false };
      }
      throw new Error('Codex CLI không trả về nội dung phản hồi.');
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  }

  if (agent.id === 'agy') {
    return executeAgyStreamOnce(agent, promptText, options);
  }

  if (agent.id === 'claude') {
    const out = execFileSync(agent.binaryPath, ['-p', '--output-format', 'text'], {
      input: promptText,
      encoding: 'utf8',
      maxBuffer: 4 * 1024 * 1024,
      timeout: 120_000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    const reply = String(out || '').trim();
    if (reply) return { reply, streamed: false };
    throw new Error('Claude CLI không trả về nội dung phản hồi.');
  }

  throw new Error(`Không hỗ trợ chạy trực tiếp Agent CLI: ${agent.id}`);
}

function isAiEnabledByPolicy() {
  const envFlag = String(process.env.NIDOAI_ENABLED ?? '').trim().toLowerCase();
  if (envFlag === '0' || envFlag === 'false' || envFlag === 'off' || envFlag === 'no') {
    return false;
  }
  if (envFlag === '1' || envFlag === 'true' || envFlag === 'on' || envFlag === 'yes') {
    return true;
  }
  const xdgConfig = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config');
  const policyPath = path.join(xdgConfig, 'ubuntu-nido', 'ai-policy.json');
  if (fs.existsSync(policyPath)) {
    try {
      const policy = JSON.parse(fs.readFileSync(policyPath, 'utf8'));
      if (policy && typeof policy.aiEnabled === 'boolean') {
        return policy.aiEnabled;
      }
    } catch {
      // ignore malformed policy file
    }
  }
  for (const dir of stateDirCandidates()) {
    const envLocal = parseEnvFile(path.join(dir, '.env.local'));
    const f = String(envLocal.NIDOAI_ENABLED ?? '').trim().toLowerCase();
    if (f === '0' || f === 'false' || f === 'off' || f === 'no') {
      return false;
    }
    if (f === '1' || f === 'true' || f === 'on' || f === 'yes') {
      return true;
    }
  }
  return true;
}

const AI_DISABLED_MESSAGE =
  'Chế độ AI trên NidoOS hiện đang TẮT. Mở NidoHub (`nido-hub --onboarding` hoặc `nido-welcome --force`) để bật lại AI và đăng nhập Tài khoản Nido hoặc ít nhất 1 tài khoản (Google/Gemini, ChatGPT hoặc Claude).';

async function cmdStatus() {
  const aiEnabled = isAiEnabledByPolicy();
  const capabilities = collectCapabilities();
  const localAccount = readSanitizedNidoAccount();
  if (!aiEnabled) {
    console.log(
      JSON.stringify(
        {
          ok: true,
          version: VERSION,
          aiEnabled: false,
          aiReady: false,
          nidoAccount: localAccount,
          capabilities,
          setupHint: AI_DISABLED_MESSAGE,
        },
        null,
        2
      )
    );
    return;
  }
  const conn = resolveGatewayConnection();
  const signedInAgents = readSignedInAgentClis();
  const healthUrl = `${conn.baseUrl}/healthz`;
  try {
    const res = await fetch(healthUrl, { signal: AbortSignal.timeout(3000) });
    const text = await res.text();
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { raw: text };
    }
    if (!res.ok) {
      console.error(JSON.stringify({ ok: false, status: res.status, healthUrl, payload }, null, 2));
      process.exit(1);
    }
    const pb = payload && typeof payload === 'object' ? payload.primaryBackend : null;
    const gatewayReady = Boolean(pb && pb.configured && pb.reachable);
    const nidoAccount =
      payload && typeof payload === 'object' && payload.nidoAccount && typeof payload.nidoAccount === 'object'
        ? sanitizeNidoAccountObject(payload.nidoAccount)
        : localAccount;
    const aiReady = gatewayReady || signedInAgents.length > 0 || nidoAccount.signedIn;
    console.log(
      JSON.stringify(
        {
          ok: true,
          version: VERSION,
          aiEnabled: true,
          healthUrl,
          runtimeFound: conn.runtimeFound,
          tokenConfigured: conn.clientToken.length >= 16,
          gatewayReady,
          nidoAccount,
          signedInAgents: signedInAgents.map((a) => a.id),
          aiReady,
          capabilities,
          ...(aiReady
            ? {}
            : {
                setupHint:
                  'Chưa cấu hình model AI trên máy. Mở NidoHub (`nido-hub --page welcome`) để đăng nhập Tài khoản Nido, Google Antigravity (Google AI Ultra/Pro/Free), OpenAI Codex / Claude Code, dán Google AI Studio GEMINI_API_KEY miễn phí ($0), hoặc cài Ollama theo cấu hình máy.',
              }),
          gateway: payload,
        },
        null,
        2
      )
    );
  } catch (err) {
    console.error(
      `nidoagent: không kết nối được tới NidoAI Gateway (${healthUrl}): ${
        err instanceof Error ? err.message : String(err)
      }`
    );
    console.error('Gợi ý: chạy `systemctl --user start nidoai-gateway` hoặc `nidoai serve`.');
    process.exit(1);
  }
}

function formatQuotaText(acct) {
  if (!acct || !acct.signedIn) {
    return 'Chưa đăng nhập Tài khoản Nido (dùng `nidoagent account login` hoặc mở NidoHub).';
  }
  const lines = [
    `Tài khoản Nido: ${acct.email || acct.userId || '(đã đăng nhập)'}`,
    `Gói dịch vụ   : ${acct.planName || acct.planCode || 'free'} (${acct.isPro ? 'Pro' : 'Free'})`,
  ];
  if (acct.quota && typeof acct.quota === 'object') {
    const q = acct.quota;
    if (typeof q.remaining === 'number' && typeof q.limit === 'number') {
      lines.push(`Quota         : còn ${q.remaining}/${q.limit} lượt`);
    } else if (typeof q.summary === 'string' && q.summary) {
      lines.push(`Quota         : ${q.summary}`);
    }
  }
  return lines.join('\n');
}

function promptTextLine(label) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    rl.question(label, (answer) => {
      rl.close();
      resolve(String(answer || '').trim());
    });
  });
}

function promptPasswordHidden(label) {
  return new Promise((resolve) => {
    if (!process.stdin.isTTY) {
      resolve('');
      return;
    }
    let muted = false;
    const rl = readline.createInterface({
      input: process.stdin,
      output: new Proxy(process.stdout, {
        get(target, prop) {
          if (prop === 'write') {
            return (chunk, ...rest) => {
              if (!muted || chunk === '\n' || chunk === '\r\n') {
                return target.write(chunk, ...rest);
              }
              return true;
            };
          }
          const v = target[prop];
          return typeof v === 'function' ? v.bind(target) : v;
        },
      }),
      terminal: true,
    });
    process.stdout.write(label);
    muted = true;
    rl.question('', (answer) => {
      muted = false;
      process.stdout.write('\n');
      rl.close();
      resolve(String(answer || ''));
    });
  });
}

async function cmdAccount(args = []) {
  let sub = 'status';
  let flagJson = false;
  let flagPasswordStdin = false;
  let flagNoPrimary = false;
  let email = '';

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === 'status' || arg === 'login' || arg === 'logout' || arg === 'sync') {
      sub = arg;
    } else if (arg === '--json') {
      flagJson = true;
    } else if (arg === '--password-stdin') {
      flagPasswordStdin = true;
    } else if (arg === '--no-primary') {
      flagNoPrimary = true;
    } else if (arg === '--email' || arg === '-e') {
      i += 1;
      if (args[i]) email = args[i].trim();
    } else if (arg === '--password' || arg.startsWith('--password=')) {
      const msg =
        'Không truyền --password trực tiếp qua tham số dòng lệnh; hãy dùng --password-stdin hoặc nhập tương tác.';
      if (flagJson) {
        console.error(JSON.stringify({ ok: false, error: msg }, null, 2));
      } else {
        console.error(msg);
      }
      process.exit(2);
    }
  }

  const conn = resolveGatewayConnection();
  const authHeaders = conn.clientToken
    ? { Authorization: `Bearer ${conn.clientToken}` }
    : {};

  if (sub === 'status') {
    let payload = null;
    let gatewayReachable = true;
    try {
      const res = await fetch(`${conn.baseUrl}/v1/account`, {
        method: 'GET',
        headers: authHeaders,
        signal: AbortSignal.timeout(4000),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const msg = data?.error?.message || data?.error || `HTTP ${res.status}`;
        if (flagJson) {
          console.error(JSON.stringify({ ok: false, status: res.status, error: msg }, null, 2));
        } else {
          console.error(`Không lấy được trạng thái Tài khoản Nido (${msg}).`);
        }
        process.exit(1);
      }
      payload = sanitizeNidoAccountObject(data);
    } catch {
      gatewayReachable = false;
      payload = readSanitizedNidoAccount();
    }
    if (flagJson) {
      console.log(
        JSON.stringify(
          {
            ok: true,
            gatewayReachable,
            ...payload,
          },
          null,
          2
        )
      );
      return;
    }
    console.log(formatQuotaText(payload));
    return;
  }

  if (sub === 'sync') {
    try {
      const res = await fetch(`${conn.baseUrl}/v1/account/sync`, {
        method: 'POST',
        headers: authHeaders,
        signal: AbortSignal.timeout(10_000),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) {
        const msg = data?.error?.message || data?.error || `HTTP ${res.status}`;
        if (flagJson) {
          console.error(JSON.stringify({ ok: false, status: res.status, error: msg }, null, 2));
        } else {
          console.error(`Đồng bộ Tài khoản Nido thất bại: ${msg}`);
        }
        process.exit(1);
      }
      const acct = sanitizeNidoAccountObject(data);
      if (flagJson) {
        console.log(JSON.stringify({ ok: true, ...acct }, null, 2));
        return;
      }
      console.log(formatQuotaText(acct));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (flagJson) {
        console.error(JSON.stringify({ ok: false, error: msg }, null, 2));
      } else {
        console.error(`Không kết nối được NidoAI Gateway (${conn.baseUrl}): ${msg}`);
      }
      process.exit(1);
    }
    return;
  }

  if (sub === 'login') {
    if (!email && process.stdin.isTTY && !flagPasswordStdin) {
      email = await promptTextLine('Email Tài khoản Nido: ');
    }
    let password = '';
    if (flagPasswordStdin) {
      password = fs.readFileSync(0, 'utf8').replace(/\r?\n$/, '');
    } else if (process.stdin.isTTY) {
      password = await promptPasswordHidden('Mật khẩu: ');
    }
    if (!email || !password) {
      const msg =
        'Thiếu email hoặc mật khẩu. Dùng `nidoagent account login --email <email> --password-stdin` hoặc nhập tương tác trên TTY.';
      if (flagJson) {
        console.error(JSON.stringify({ ok: false, error: msg }, null, 2));
      } else {
        console.error(msg);
      }
      process.exit(2);
    }
    try {
      const res = await fetch(`${conn.baseUrl}/api/admin/nido-account/login`, {
        method: 'POST',
        headers: { ...authHeaders, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email,
          password,
          setAsPrimary: !flagNoPrimary,
        }),
        signal: AbortSignal.timeout(15_000),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) {
        const msg = data?.error?.message || data?.error || `HTTP ${res.status}`;
        if (flagJson) {
          console.error(JSON.stringify({ ok: false, status: res.status, error: msg }, null, 2));
        } else {
          console.error(`Đăng nhập thất bại: ${msg}`);
        }
        process.exit(1);
      }
      const acct = sanitizeNidoAccountObject(data.account || data);
      if (flagJson) {
        console.log(JSON.stringify({ ok: true, ...acct }, null, 2));
        return;
      }
      console.log(`Đã đăng nhập Tài khoản Nido thành công.\n${formatQuotaText(acct)}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (flagJson) {
        console.error(JSON.stringify({ ok: false, error: msg }, null, 2));
      } else {
        console.error(`Không kết nối được NidoAI Gateway (${conn.baseUrl}): ${msg}`);
      }
      process.exit(1);
    }
    return;
  }

  if (sub === 'logout') {
    try {
      const res = await fetch(`${conn.baseUrl}/api/admin/nido-account/logout`, {
        method: 'POST',
        headers: { ...authHeaders, 'Content-Type': 'application/json' },
        signal: AbortSignal.timeout(10_000),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) {
        const msg = data?.error?.message || data?.error || `HTTP ${res.status}`;
        if (flagJson) {
          console.error(JSON.stringify({ ok: false, status: res.status, error: msg }, null, 2));
        } else {
          console.error(`Đăng xuất thất bại: ${msg}`);
        }
        process.exit(1);
      }
      if (flagJson) {
        console.log(JSON.stringify({ ok: true, signedIn: false }, null, 2));
        return;
      }
      console.log('Đã đăng xuất Tài khoản Nido khỏi máy này.');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (flagJson) {
        console.error(JSON.stringify({ ok: false, error: msg }, null, 2));
      } else {
        console.error(`Không kết nối được NidoAI Gateway (${conn.baseUrl}): ${msg}`);
      }
      process.exit(1);
    }
  }
}

function cmdCapabilities() {
  console.log(
    JSON.stringify(
      {
        ok: true,
        version: VERSION,
        aiEnabled: isAiEnabledByPolicy(),
        capabilities: collectCapabilities(),
      },
      null,
      2
    )
  );
}

function cmdSkills(args = []) {
  let sub = 'list';
  let flagJson = false;
  let flagAll = false;
  for (const arg of args) {
    if (arg === 'list' || arg === 'sync') {
      sub = arg;
    } else if (arg === '--json') {
      flagJson = true;
    } else if (arg === '--all') {
      flagAll = true;
    }
  }
  if (sub === 'sync') {
    const res = syncOsSkills({ syncAll: flagAll });
    if (flagJson) {
      console.log(JSON.stringify(res, null, 2));
      return;
    }
    const activeTargets = res.targets.filter((t) => t.active);
    console.log(`NidoOS OS Skills (${res.skillsCount} skill từ ${res.sourceDir}):`);
    for (const t of activeTargets) {
      const counts = t.items.reduce((acc, item) => {
        acc[item.status] = (acc[item.status] || 0) + 1;
        return acc;
      }, {});
      const summary = Object.entries(counts)
        .map(([k, v]) => `${k}=${v}`)
        .join(', ');
      console.log(`  - [${t.target}] ${t.dir}: ${summary || 'none'}`);
    }
    return;
  }

  const { dir, skills } = listOsSkills();
  if (flagJson) {
    console.log(JSON.stringify({ ok: true, dir, count: skills.length, skills }, null, 2));
    return;
  }
  if (skills.length === 0) {
    console.log(`Không tìm thấy NidoOS OS Skills trong ${dir}.`);
    return;
  }
  console.log(`NidoOS OS Skills (${skills.length} từ ${dir}):`);
  for (const s of skills) {
    console.log(`  - ${s.name}: ${s.description || s.skillFile}`);
  }
}

function readAttachedFile(filePath) {
  const resolved = path.resolve(filePath);
  const stat = fs.statSync(resolved);
  if (!stat.isFile()) {
    throw new Error(`Tệp đính kèm không hợp lệ (không phải tệp thông thường): ${resolved}`);
  }
  const ext = path.extname(resolved).toLowerCase();
  if (OFFICE_EXTS.has(ext)) {
    const officeBin = process.env.NIDOOFFICE_BIN || 'nidooffice';
    let extracted;
    try {
      extracted = execFileSync(officeBin, ['read', resolved], {
        encoding: 'utf8',
        maxBuffer: 4 * 1024 * 1024,
        env: process.env,
      });
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      throw new Error(
        `Không đọc được tệp ${ext} (${resolved}). Nếu tệp là PDF/ODF/Office cũ, hãy kiểm tra NidoOffice Suite. Chi tiết: ${detail}`
      );
    }
    const rawBuf = Buffer.from(extracted, 'utf8');
    const truncated = rawBuf.length > MAX_ATTACH_BYTES;
    const text = rawBuf.subarray(0, MAX_ATTACH_BYTES).toString('utf8');
    return [
      `--- File: ${resolved} (extracted via nidooffice${truncated ? ', truncated to 64 KiB' : ''}) ---`,
      text,
      '--- End of file ---',
    ].join('\n');
  }
  const raw = fs.readFileSync(resolved);
  const truncated = raw.length > MAX_ATTACH_BYTES;
  const text = raw.subarray(0, MAX_ATTACH_BYTES).toString('utf8');
  return [
    `--- File: ${resolved} (${stat.size} bytes${truncated ? ', truncated to 64 KiB' : ''}) ---`,
    text,
    '--- End of file ---',
  ].join('\n');
}

async function isGatewayReadyFast(conn) {
  try {
    const res = await fetch(`${conn.baseUrl}/healthz`, { signal: AbortSignal.timeout(1000) });
    if (!res.ok) return false;
    const payload = await res.json().catch(() => null);
    const pb = payload && typeof payload === 'object' ? payload.primaryBackend : null;
    return Boolean(pb && pb.configured && pb.reachable);
  } catch {
    return false;
  }
}

async function sendChatCompletion(conn, messages, options = {}) {
  const fallbackAgents = readSignedInAgentClis();
  if (conn.clientToken && conn.clientToken.length >= 16) {
    try {
      const res = await fetch(`${conn.baseUrl}/v1/chat/completions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${conn.clientToken}`,
        },
        body: JSON.stringify({
          model: options.model || process.env.NIDO_MODEL || 'auto',
          messages,
          stream: false,
        }),
      });

      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        const content = data?.choices?.[0]?.message?.content;
        if (typeof content !== 'string') {
          throw new Error('Phản hồi từ Gateway không chứa choices[0].message.content');
        }
        return {
          reply: content,
          streamed: false,
          backend: 'gateway',
          model: typeof data?.model === 'string' ? data.model : options.model || process.env.NIDO_MODEL || 'auto',
        };
      }

      const msg = data?.error?.message || `HTTP ${res.status}`;
      const unconfigured = res.status === 503 || /not configured|not responding|missing.*key/i.test(msg);
      if (unconfigured && fallbackAgents.length > 0) {
        const cliRes = await executeViaOfficialAgentCli(fallbackAgents[0], messages, options);
        return { ...cliRes, backend: fallbackAgents[0].id, model: options.model || 'default' };
      }
      if (unconfigured) {
        throw new Error(
          `Chưa cấu hình AI sẵn sàng (${msg}).\n` +
            'Gợi ý thiết lập nhanh (30 giây): chạy `nido-hub --page welcome` để đăng nhập Google Antigravity (Ultra/Pro/Free), dán Google AI Studio GEMINI_API_KEY miễn phí ($0), đăng nhập OpenAI Codex / Claude Code, hoặc tải model cục bộ Ollama.'
        );
      }
      throw new Error(`Lỗi NidoAI Gateway: ${msg}`);
    } catch (err) {
      if (fallbackAgents.length > 0 && err instanceof TypeError) {
        const cliRes = await executeViaOfficialAgentCli(fallbackAgents[0], messages, options);
        return { ...cliRes, backend: fallbackAgents[0].id, model: options.model || 'default' };
      }
      throw err;
    }
  }

  if (fallbackAgents.length > 0) {
    const cliRes = await executeViaOfficialAgentCli(fallbackAgents[0], messages, options);
    return { ...cliRes, backend: fallbackAgents[0].id, model: options.model || 'default' };
  }
  throw new Error(
    'Chưa có khóa AI hay tài khoản Agent đã đăng nhập. Hãy mở NidoHub (`nido-hub --page welcome`) để đăng nhập Google Antigravity (Ultra/Pro/Free), dán Google AI Studio GEMINI_API_KEY miễn phí ($0) hoặc đăng nhập OpenAI Codex / Claude Code.'
  );
}

function emitAskError(message, asJson, exitCode = 1) {
  if (asJson) {
    console.error(JSON.stringify({ ok: false, error: message }));
  } else {
    console.error(message);
  }
  process.exit(exitCode);
}

async function cmdAsk(args) {
  const flagJsonEarly = args.includes('--json');
  if (!isAiEnabledByPolicy()) {
    emitAskError(AI_DISABLED_MESSAGE, flagJsonEarly, 1);
  }
  const files = [];
  const promptParts = [];
  let outPath = '';
  let model = '';
  let effort = '';
  let flagPlain = false;
  let flagRaw = false;
  let flagJson = false;

  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === '--file' || args[i] === '-f') {
      i += 1;
      if (!args[i]) {
        emitAskError('nidoagent ask: thiếu đường dẫn sau --file', flagJsonEarly, 2);
      }
      files.push(args[i]);
    } else if (args[i] === '--out' || args[i] === '-o') {
      i += 1;
      if (!args[i]) {
        emitAskError('nidoagent ask: thiếu đường dẫn sau --out', flagJsonEarly, 2);
      }
      outPath = args[i];
    } else if (args[i] === '--model' || args[i] === '-m') {
      i += 1;
      if (!args[i]) {
        emitAskError('nidoagent ask: thiếu tên model sau --model', flagJsonEarly, 2);
      }
      model = args[i];
    } else if (args[i] === '--effort') {
      i += 1;
      if (!args[i]) {
        emitAskError('nidoagent ask: thiếu mức suy luận sau --effort (low|medium|high|xhigh|max)', flagJsonEarly, 2);
      }
      effort = args[i];
    } else if (args[i] === '--json') {
      flagJson = true;
    } else if (args[i] === '--plain') {
      flagPlain = true;
    } else if (args[i] === '--raw') {
      flagRaw = true;
    } else {
      promptParts.push(args[i]);
    }
  }

  const prompt = promptParts.join(' ').trim();
  if (!prompt && files.length === 0) {
    emitAskError(
      'Usage: nidoagent ask [--model <id>] [--effort <lvl>] [--json|--plain|--raw] [--file <path>] [--out <path>] <prompt>',
      flagJson,
      2
    );
  }

  let sections;
  try {
    sections = [];
    for (const f of files) {
      sections.push(readAttachedFile(f));
    }
    sections.push(prompt || 'Hãy tóm tắt và phân tích nội dung các tệp đính kèm.');
  } catch (err) {
    emitAskError(err instanceof Error ? err.message : String(err), flagJson, 1);
  }

  const outExt = outPath ? path.extname(outPath).toLowerCase() : '';
  let systemPrompt =
    'Bạn là NidoAgent — trợ lý điều phối công việc mặc định trên hệ điều hành NidoOS. Trả lời ngắn gọn, chính xác, ưu tiên tiếng Việt khi người dùng hỏi bằng tiếng Việt.';
  if (outExt === '.xlsx') {
    systemPrompt +=
      ' Khi xuất báo cáo bảng tính (.xlsx), hãy trình bày dữ liệu chính dưới dạng bảng Markdown (| Cột | Giá trị |) để hệ thống trích xuất sang Excel chính xác.';
  }

  const conn = resolveGatewayConnection();
  const outMode = resolveOutputMode(flagPlain, flagRaw);
  const writer = new MarkdownStreamWriter(outMode, (s) => process.stdout.write(s));
  let completion;
  try {
    completion = await sendChatCompletion(
      conn,
      [
        {
          role: 'system',
          content: systemPrompt,
        },
        { role: 'user', content: sections.join('\n\n') },
      ],
      {
        model,
        effort,
        ...(flagJson ? {} : { onDelta: (delta) => writer.push(delta) }),
      }
    );
  } catch (err) {
    emitAskError(err instanceof Error ? err.message : String(err), flagJson, 1);
  }

  const { reply, streamed, backend, model: resolvedModel } = completion;

  let resolvedOut = '';
  if (outPath) {
    try {
      resolvedOut = path.resolve(outPath);
      fs.mkdirSync(path.dirname(resolvedOut), { recursive: true });
      if (outExt === '.docx' || outExt === '.xlsx') {
        const officeBin = process.env.NIDOOFFICE_BIN || 'nidooffice';
        execFileSync(officeBin, ['create', '--type', outExt.slice(1), '--out', resolvedOut, '--from', '-'], {
          input: reply,
          encoding: 'utf8',
          env: process.env,
        });
      } else {
        fs.writeFileSync(resolvedOut, `${reply}\n`, 'utf8');
      }
    } catch (err) {
      emitAskError(err instanceof Error ? err.message : String(err), flagJson, 1);
    }
  }

  if (flagJson) {
    console.log(
      JSON.stringify(
        {
          ok: true,
          version: VERSION,
          backend: backend || 'gateway',
          model: resolvedModel || model || 'auto',
          reply,
          attachedFiles: files.map((f) => path.resolve(f)),
          ...(resolvedOut ? { outPath: resolvedOut } : {}),
        },
        null,
        2
      )
    );
    return;
  }

  if (streamed) {
    writer.flush();
  } else {
    console.log(renderMarkdownFull(reply, outMode));
  }

  if (resolvedOut) {
    console.log(`\n[Đã xuất báo cáo: ${resolvedOut}]`);
  }
}

async function cmdChat(args = []) {
  if (!isAiEnabledByPolicy()) {
    console.error(AI_DISABLED_MESSAGE);
    process.exit(1);
  }
  let model = '';
  let effort = '';
  let flagPlain = false;
  let flagRaw = false;
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === '--model' || args[i] === '-m') {
      i += 1;
      if (args[i]) model = args[i];
    } else if (args[i] === '--effort') {
      i += 1;
      if (args[i]) effort = args[i];
    } else if (args[i] === '--plain') {
      flagPlain = true;
    } else if (args[i] === '--raw') {
      flagRaw = true;
    }
  }
  const outMode = resolveOutputMode(flagPlain, flagRaw);

  const conn = resolveGatewayConnection();
  const fallbackAgents = readSignedInAgentClis();
  const gwReady = await isGatewayReadyFast(conn);
  const agyAgent = !gwReady ? fallbackAgents.find((a) => a.id === 'agy') : null;
  const warmAgy = agyAgent ? new AgyWarmSession(agyAgent, { model, effort }) : null;
  if (warmAgy) {
    // Pre-warm agy process immediately while user types their first prompt.
    warmAgy.ensureStarted();
  }

  const messages = [
    {
      role: 'system',
      content:
        'Bạn là NidoAgent — trợ lý điều phối công việc trên NidoOS. Trả lời rõ ràng, hữu ích.',
    },
  ];
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: 'nidoagent> ',
  });
  let rlClosed = false;
  rl.on('close', () => {
    rlClosed = true;
  });
  const safePrompt = () => {
    if (!rlClosed) {
      try {
        rl.prompt();
      } catch {
        // ignore closed readline on piped stdin
      }
    }
  };
  const modeNote = warmAgy
    ? `Google Antigravity Warm Session (${model || process.env.NIDO_AGY_MODEL || 'gemini-3.8-flash-low'})`
    : conn.baseUrl;
  console.log(`NidoOS Agent Chat (${modeNote}) — gõ "exit" để thoát.`);
  safePrompt();

  const cleanup = () => {
    if (warmAgy) warmAgy.close();
  };
  process.on('SIGINT', () => {
    cleanup();
    process.exit(0);
  });

  try {
    let firstWarmTurn = true;
    for await (const line of rl) {
      const trimmed = line.trim();
      if (!trimmed) {
        safePrompt();
        continue;
      }
      if (trimmed === 'exit' || trimmed === 'quit' || trimmed === '/exit') {
        break;
      }
      messages.push({ role: 'user', content: trimmed });
      try {
        const writer = new MarkdownStreamWriter(outMode, (s) => process.stdout.write(s));
        let resObj;
        if (warmAgy) {
          const turnPrompt = firstWarmTurn
            ? `[System]\n${messages[0].content}\n\n${trimmed}`
            : trimmed;
          firstWarmTurn = false;
          process.stdout.write('\n');
          resObj = await warmAgy.sendTurn(turnPrompt, (delta) => writer.push(delta));
        } else {
          resObj = await sendChatCompletion(conn, messages, {
            model,
            effort,
            onDelta: (delta) => {
              if (!writer.wroteAny) process.stdout.write('\n');
              writer.push(delta);
            },
          });
        }
        messages.push({ role: 'assistant', content: resObj.reply });
        if (resObj.streamed) {
          writer.flush();
          process.stdout.write('\n');
        } else {
          console.log(`\n${renderMarkdownFull(resObj.reply, outMode)}\n`);
        }
      } catch (err) {
        messages.pop();
        console.error(`Lỗi: ${err instanceof Error ? err.message : String(err)}`);
      }
      safePrompt();
    }
  } finally {
    cleanup();
    if (!rlClosed) rl.close();
  }
}

async function main() {
  const [, , cmd, ...rest] = process.argv;
  if (!cmd || cmd === '--help' || cmd === '-h' || cmd === 'help') {
    printHelp();
    return;
  }
  if (cmd === '--version' || cmd === '-v' || cmd === 'version') {
    console.log(`nidoagent ${VERSION}`);
    return;
  }
  if (cmd === 'status' || cmd === 'doctor') {
    await cmdStatus();
    return;
  }
  if (cmd === 'account') {
    await cmdAccount(rest);
    return;
  }
  if (cmd === 'capabilities' || cmd === 'caps') {
    cmdCapabilities();
    return;
  }
  if (cmd === 'skills') {
    cmdSkills(rest);
    return;
  }
  if (cmd === 'ask') {
    await cmdAsk(rest);
    return;
  }
  if (cmd === 'chat') {
    await cmdChat(rest);
    return;
  }

  console.error(`Lệnh không hỗ trợ: ${cmd}. Chạy 'nidoagent --help'.`);
  process.exit(1);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
