#!/usr/bin/env node
/**
 * NidoAgent CLI (Ubuntu-Nido)
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
  console.log(`NidoAgent CLI v${VERSION} (Ubuntu-Nido)

Usage:
  nidoagent status | doctor                                            Kiểm tra kết nối NidoAI Gateway & Agent CLI
  nidoagent ask [--model <id>] [--effort <lvl>] [--plain|--raw] [--file <p>] [--out <p>] <prompt>
                                                                       Gửi câu hỏi / tệp (streaming nhanh) & xuất báo cáo
  nidoagent chat [--model <id>] [--effort <lvl>] [--plain|--raw]       Mở phiên hội thoại giữ ấm (warm session)
  nidoagent --version | --help                                         Hiện thông tin phiên bản / trợ giúp
`);
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
  'Chế độ AI trên Ubuntu-Nido hiện đang TẮT. Mở NidoHub (`nido-hub --onboarding` hoặc `nido-welcome --force`) để bật lại AI và đăng nhập ít nhất 1 tài khoản (Google/Gemini, ChatGPT hoặc Claude).';

async function cmdStatus() {
  const aiEnabled = isAiEnabledByPolicy();
  if (!aiEnabled) {
    console.log(
      JSON.stringify(
        {
          ok: true,
          version: VERSION,
          aiEnabled: false,
          aiReady: false,
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
    const aiReady = gatewayReady || signedInAgents.length > 0;
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
          signedInAgents: signedInAgents.map((a) => a.id),
          aiReady,
          ...(aiReady
            ? {}
            : {
                setupHint:
                  'Chưa cấu hình model AI trên máy. Mở NidoHub (`nido-hub --page welcome`) để đăng nhập Google Antigravity (Google AI Ultra/Pro/Free), OpenAI Codex / Claude Code, dán Google AI Studio GEMINI_API_KEY miễn phí ($0), hoặc cài Ollama theo cấu hình máy.',
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
        return { reply: content, streamed: false };
      }

      const msg = data?.error?.message || `HTTP ${res.status}`;
      const unconfigured = res.status === 503 || /not configured|not responding|missing.*key/i.test(msg);
      if (unconfigured && fallbackAgents.length > 0) {
        return executeViaOfficialAgentCli(fallbackAgents[0], messages, options);
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
        return executeViaOfficialAgentCli(fallbackAgents[0], messages, options);
      }
      throw err;
    }
  }

  if (fallbackAgents.length > 0) {
    return executeViaOfficialAgentCli(fallbackAgents[0], messages, options);
  }
  throw new Error(
    'Chưa có khóa AI hay tài khoản Agent đã đăng nhập. Hãy mở NidoHub (`nido-hub --page welcome`) để đăng nhập Google Antigravity (Ultra/Pro/Free), dán Google AI Studio GEMINI_API_KEY miễn phí ($0) hoặc đăng nhập OpenAI Codex / Claude Code.'
  );
}

async function cmdAsk(args) {
  if (!isAiEnabledByPolicy()) {
    console.error(AI_DISABLED_MESSAGE);
    process.exit(1);
  }
  const files = [];
  const promptParts = [];
  let outPath = '';
  let model = '';
  let effort = '';
  let flagPlain = false;
  let flagRaw = false;

  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === '--file' || args[i] === '-f') {
      i += 1;
      if (!args[i]) {
        console.error('nidoagent ask: thiếu đường dẫn sau --file');
        process.exit(2);
      }
      files.push(args[i]);
    } else if (args[i] === '--out' || args[i] === '-o') {
      i += 1;
      if (!args[i]) {
        console.error('nidoagent ask: thiếu đường dẫn sau --out');
        process.exit(2);
      }
      outPath = args[i];
    } else if (args[i] === '--model' || args[i] === '-m') {
      i += 1;
      if (!args[i]) {
        console.error('nidoagent ask: thiếu tên model sau --model');
        process.exit(2);
      }
      model = args[i];
    } else if (args[i] === '--effort') {
      i += 1;
      if (!args[i]) {
        console.error('nidoagent ask: thiếu mức suy luận sau --effort (low|medium|high|xhigh|max)');
        process.exit(2);
      }
      effort = args[i];
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
    console.error(
      'Usage: nidoagent ask [--model <id>] [--effort <lvl>] [--plain|--raw] [--file <path>] [--out <path>] <prompt>'
    );
    process.exit(2);
  }

  const sections = [];
  for (const f of files) {
    sections.push(readAttachedFile(f));
  }
  sections.push(prompt || 'Hãy tóm tắt và phân tích nội dung các tệp đính kèm.');

  const outExt = outPath ? path.extname(outPath).toLowerCase() : '';
  let systemPrompt =
    'Bạn là NidoAgent — trợ lý điều phối công việc mặc định trên hệ điều hành Ubuntu-Nido. Trả lời ngắn gọn, chính xác, ưu tiên tiếng Việt khi người dùng hỏi bằng tiếng Việt.';
  if (outExt === '.xlsx') {
    systemPrompt +=
      ' Khi xuất báo cáo bảng tính (.xlsx), hãy trình bày dữ liệu chính dưới dạng bảng Markdown (| Cột | Giá trị |) để hệ thống trích xuất sang Excel chính xác.';
  }

  const conn = resolveGatewayConnection();
  const outMode = resolveOutputMode(flagPlain, flagRaw);
  const writer = new MarkdownStreamWriter(outMode, (s) => process.stdout.write(s));
  const { reply, streamed } = await sendChatCompletion(
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
      onDelta: (delta) => writer.push(delta),
    }
  );

  if (streamed) {
    writer.flush();
  } else {
    console.log(renderMarkdownFull(reply, outMode));
  }

  if (outPath) {
    const resolvedOut = path.resolve(outPath);
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
        'Bạn là NidoAgent — trợ lý điều phối công việc trên Ubuntu-Nido. Trả lời rõ ràng, hữu ích.',
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
  console.log(`Ubuntu-Nido Agent Chat (${modeNote}) — gõ "exit" để thoát.`);
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
