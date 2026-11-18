#!/usr/bin/env node
/**
 * NidoAI Local Gateway (Ubuntu-Nido Open-Source Edition)
 *
 * Binds strictly to 127.0.0.1, enforces constant-time Bearer token auth,
 * restricts upstream providers to HTTPS or loopback URLs (SSRF-safe), and
 * masks secrets in CLI diagnostics.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';

const PKG_VERSION = '0.1.0';
const MAX_BODY_BYTES = 1_048_576; // 1 MiB
const MIN_TOKEN_LENGTH = 16;

const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Cache-Control': 'no-store',
};

function maskSecret(secret) {
  if (!secret) return '';
  if (secret.length <= 10) return '••••••••';
  return `${secret.slice(0, 6)}••••${secret.slice(-4)}`;
}

function readEnvFile(filePath) {
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

function isAllowedUpstreamUrl(rawUrl) {
  try {
    const u = new URL(rawUrl);
    if (u.protocol === 'https:') return true;
    if (
      u.protocol === 'http:' &&
      (u.hostname === '127.0.0.1' || u.hostname === 'localhost' || u.hostname === '::1' || u.hostname === '[::1]')
    ) {
      return true;
    }
    return false;
  } catch {
    return false;
  }
}

function stateDir() {
  return process.env.NIDOAI_HOME || path.join(os.homedir(), '.nidoai');
}

function runtimePath() {
  return path.join(stateDir(), 'runtime.json');
}

function ensureClientToken(dir, existingToken) {
  if (existingToken.length >= MIN_TOKEN_LENGTH) return existingToken;
  const generated = `nidoai_${crypto.randomBytes(24).toString('hex')}`;
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  fs.chmodSync(dir, 0o700);
  const envLocalPath = path.join(dir, '.env.local');
  const lines = fs.existsSync(envLocalPath)
    ? fs
        .readFileSync(envLocalPath, 'utf8')
        .split(/\r?\n/)
        .filter((l) => l.trim() && !l.trim().startsWith('NIDOAI_CLIENT_TOKEN='))
    : [];
  lines.push(`NIDOAI_CLIENT_TOKEN=${generated}`);
  fs.writeFileSync(envLocalPath, `${lines.join('\n')}\n`, { encoding: 'utf8', mode: 0o600 });
  fs.chmodSync(envLocalPath, 0o600);
  return generated;
}

function loadGatewayConfig(bootstrapToken) {
  const dir = stateDir();
  const fileEnv = {
    ...readEnvFile(path.join(process.cwd(), '.env')),
    ...readEnvFile(path.join(process.cwd(), '.env.local')),
    ...readEnvFile(path.join(dir, '.env.local')),
  };
  const get = (k, fb = '') => (process.env[k] ?? fileEnv[k] ?? fb).trim();

  const port = Number.parseInt(get('NIDOAI_PORT', '18090'), 10) || 18090;
  let token = get('NIDOAI_CLIENT_TOKEN');
  if (bootstrapToken) {
    token = ensureClientToken(dir, token);
  }

  const rawPrimary = get('NIDOAI_PRIMARY_BACKEND', 'gemini');
  const primaryBackend =
    rawPrimary === 'gemini' || rawPrimary === 'openai' || rawPrimary === 'local'
      ? rawPrimary
      : 'gemini';

  const geminiKey = get('GEMINI_API_KEY');
  const openaiKey = get('OPENAI_API_KEY');
  const localUrlRaw = get('NIDOAI_LOCAL_BASE_URL');
  const localUrl = localUrlRaw || 'http://127.0.0.1:11434/v1';

  const geminiUrl = get('NIDOAI_GEMINI_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/openai').replace(/\/+$/, '');
  const openaiUrl = get('NIDOAI_OPENAI_BASE_URL', 'https://api.openai.com/v1').replace(/\/+$/, '');

  const backends = {
    gemini: {
      kind: 'gemini',
      baseUrl: geminiUrl,
      apiKey: geminiKey,
      defaultModel: get('NIDOAI_GEMINI_MODEL', 'gemini-2.5-flash'),
      configured: Boolean(geminiKey) && isAllowedUpstreamUrl(geminiUrl),
    },
    openai: {
      kind: 'openai',
      baseUrl: openaiUrl,
      apiKey: openaiKey,
      defaultModel: get('NIDOAI_OPENAI_MODEL', 'gpt-4o-mini'),
      configured: Boolean(openaiKey) && isAllowedUpstreamUrl(openaiUrl),
    },
    local: {
      kind: 'local',
      baseUrl: localUrl.replace(/\/+$/, ''),
      apiKey: get('NIDOAI_LOCAL_API_KEY', 'local-no-key'),
      defaultModel: get('NIDOAI_LOCAL_MODEL', 'llama3.2'),
      configured: Boolean(localUrlRaw) && isAllowedUpstreamUrl(localUrl),
    },
  };

  return {
    host: '127.0.0.1',
    port,
    clientToken: token,
    primaryBackend,
    backends,
  };
}

function timingSafeBearerMatch(authHeader, expectedToken) {
  if (!authHeader || !expectedToken) return false;
  const prefix = 'Bearer ';
  if (!authHeader.startsWith(prefix)) return false;
  const presented = Buffer.from(authHeader.slice(prefix.length).trim(), 'utf8');
  const expected = Buffer.from(expectedToken, 'utf8');
  if (presented.length !== expected.length) return false;
  return crypto.timingSafeEqual(presented, expected);
}

function sendJson(res, status, payload, isHead = false) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    ...SECURITY_HEADERS,
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body).toString(),
  });
  res.end(isHead ? undefined : body);
}

async function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    req.on('data', (chunk) => {
      total += chunk.length;
      if (total > MAX_BODY_BYTES) {
        req.destroy();
        reject(Object.assign(new Error('Request body exceeds 1 MiB limit'), { statusCode: 413 }));
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      try {
        const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          reject(Object.assign(new Error('JSON object body required'), { statusCode: 400 }));
          return;
        }
        resolve(parsed);
      } catch {
        reject(Object.assign(new Error('Invalid JSON payload'), { statusCode: 400 }));
      }
    });
    req.on('error', (err) => reject(err));
  });
}

function resolveBackend(requestedModel, cfg) {
  if (typeof requestedModel === 'string' && requestedModel.includes('/')) {
    const [prefix, rest] = requestedModel.split('/', 2);
    if ((prefix === 'gemini' || prefix === 'openai' || prefix === 'local') && rest) {
      return { backend: cfg.backends[prefix], model: rest };
    }
  }
  const backend = cfg.backends[cfg.primaryBackend];
  const model =
    typeof requestedModel === 'string' &&
    requestedModel.trim() !== '' &&
    requestedModel !== 'auto' &&
    requestedModel !== 'default'
      ? requestedModel.trim()
      : backend.defaultModel;
  return { backend, model };
}

async function main() {
  const cmd = process.argv[2] ?? 'doctor';

  if (cmd === 'doctor') {
    const cfg = loadGatewayConfig(false);
    console.log(`=== NidoAI Doctor (v${PKG_VERSION}) ===`);
    console.log(
      `[${cfg.clientToken.length >= MIN_TOKEN_LENGTH ? 'ok          ' : 'unconfigured'}] clientToken              -> ${
        cfg.clientToken.length >= MIN_TOKEN_LENGTH ? maskSecret(cfg.clientToken) : 'not yet bootstrapped'
      }`
    );
    for (const kind of ['gemini', 'openai', 'local']) {
      const b = cfg.backends[kind];
      console.log(
        `[${b.configured ? 'ok          ' : 'unconfigured'}] ${kind.padEnd(24)} -> ${
          b.configured ? `ready (${b.defaultModel})` : 'not configured'
        }`
      );
    }
    return;
  }

  if (cmd === 'status') {
    const rp = runtimePath();
    if (!fs.existsSync(rp)) {
      console.error(`NidoAI Gateway: NOT ACTIVATED (${rp} not found)`);
      process.exit(1);
    }
    const parsed = JSON.parse(fs.readFileSync(rp, 'utf8'));
    let alive = false;
    if (typeof parsed.pid === 'number' && parsed.pid > 0) {
      try {
        process.kill(parsed.pid, 0);
        alive = true;
      } catch {
        alive = false;
      }
    }
    console.log(
      JSON.stringify(
        {
          activated: alive && typeof parsed.clientToken === 'string' && parsed.clientToken.length >= MIN_TOKEN_LENGTH,
          runtimePath: rp,
          baseUrl: parsed.baseUrl ?? '',
          maskedToken: maskSecret(parsed.clientToken ?? ''),
          pid: parsed.pid ?? 0,
          processAlive: alive,
          updatedAt: parsed.updatedAt ?? '',
        },
        null,
        2
      )
    );
    return;
  }

  if (cmd === 'deactivate') {
    const rp = runtimePath();
    if (fs.existsSync(rp)) {
      try {
        const parsed = JSON.parse(fs.readFileSync(rp, 'utf8'));
        if (typeof parsed.pid === 'number' && parsed.pid > 0) {
          process.kill(parsed.pid, 'SIGTERM');
        }
      } catch {
        // ignore stale pid/file
      }
      fs.unlinkSync(rp);
    }
    console.log(`Deactivated NidoAI Gateway (${rp} removed).`);
    return;
  }

  if (cmd === 'serve') {
    const cfg = loadGatewayConfig(true);
    const server = http.createServer(async (req, res) => {
      const url = new URL(req.url ?? '/', `http://${cfg.host}:${cfg.port}`);
      const isHead = req.method === 'HEAD';

      if ((req.method === 'GET' || isHead) && url.pathname === '/healthz') {
        const primary = cfg.backends[cfg.primaryBackend];
        sendJson(
          res,
          200,
          {
            status: 'ok',
            version: PKG_VERSION,
            bind: `${cfg.host}:${cfg.port}`,
            primaryBackend: {
              kind: primary.kind,
              configured: primary.configured,
              defaultModel: primary.defaultModel,
            },
            supportedEndpoints: ['GET /healthz', 'GET /v1/models', 'POST /v1/chat/completions'],
          },
          isHead
        );
        return;
      }

      if ((req.method === 'GET' || isHead) && url.pathname === '/v1/models') {
        if (!timingSafeBearerMatch(req.headers.authorization, cfg.clientToken)) {
          sendJson(res, 401, { error: { message: 'Unauthorized', type: 'auth_error' } }, isHead);
          return;
        }
        sendJson(
          res,
          200,
          {
            object: 'list',
            data: Object.values(cfg.backends).map((b) => ({
              id: `${b.kind}/${b.defaultModel}`,
              object: 'model',
              owned_by: b.kind,
              configured: b.configured,
            })),
          },
          isHead
        );
        return;
      }

      if (req.method === 'POST' && url.pathname === '/v1/chat/completions') {
        if (!timingSafeBearerMatch(req.headers.authorization, cfg.clientToken)) {
          sendJson(res, 401, { error: { message: 'Unauthorized', type: 'auth_error' } });
          return;
        }
        let body;
        try {
          body = await readJsonBody(req);
        } catch (err) {
          const code = typeof err === 'object' && err !== null && typeof err.statusCode === 'number' ? err.statusCode : 400;
          sendJson(res, code, {
            error: { message: err instanceof Error ? err.message : 'Bad Request', type: 'invalid_request_error' },
          });
          return;
        }

        if (!Array.isArray(body.messages) || body.messages.length === 0) {
          sendJson(res, 400, { error: { message: 'messages array is required', type: 'invalid_request_error' } });
          return;
        }

        const { backend, model } = resolveBackend(body.model, cfg);
        if (!backend.configured || !isAllowedUpstreamUrl(backend.baseUrl)) {
          sendJson(res, 503, {
            error: {
              message: `Chưa cấu hình khóa API cho nguồn '${backend.kind}'. Hãy mở Trung tâm NidoHub → Thiết lập AI để nhập khóa API hoặc chọn Local Ollama.`,
              type: 'backend_unconfigured',
            },
          });
          return;
        }

        try {
          const upstreamRes = await fetch(`${backend.baseUrl}/chat/completions`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${backend.apiKey}`,
            },
            body: JSON.stringify({
              model,
              messages: body.messages,
              temperature: typeof body.temperature === 'number' ? body.temperature : undefined,
              max_tokens: typeof body.max_tokens === 'number' ? body.max_tokens : undefined,
              stream: false,
            }),
            signal: AbortSignal.timeout(60_000),
          });
          const upstreamText = await upstreamRes.text();
          res.writeHead(upstreamRes.status, {
            ...SECURITY_HEADERS,
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': Buffer.byteLength(upstreamText).toString(),
          });
          res.end(upstreamText);
        } catch {
          sendJson(res, 502, {
            error: { message: 'Upstream AI provider request failed', type: 'upstream_error' },
          });
        }
        return;
      }

      sendJson(res, 404, { error: { message: 'Not Found', type: 'not_found' } }, isHead);
    });

    await new Promise((resolve) => server.listen(cfg.port, cfg.host, resolve));
    const dir = stateDir();
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    fs.chmodSync(dir, 0o700);
    const rp = runtimePath();
    fs.writeFileSync(
      rp,
      JSON.stringify(
        {
          baseUrl: `http://${cfg.host}:${cfg.port}`,
          clientToken: cfg.clientToken,
          pid: process.pid,
          updatedAt: new Date().toISOString(),
        },
        null,
        2
      ),
      { encoding: 'utf8', mode: 0o600 }
    );
    fs.chmodSync(rp, 0o600);
    console.log(JSON.stringify({ event: 'gateway_started', listen: `http://${cfg.host}:${cfg.port}` }));
    const shutdown = () => server.close(() => process.exit(0));
    process.once('SIGINT', shutdown);
    process.once('SIGTERM', shutdown);
    return;
  }

  console.error(`Unknown command: ${cmd}. Usage: nidoai <doctor|serve|status|deactivate>`);
  process.exit(1);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
