import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { CHAT_BACKEND_KINDS, MIN_CLIENT_TOKEN_LENGTH, PKG_VERSION, isChatBackendKind, loadConfig, maskSecret, saveConfigUpdatesToEnvLocal, } from '../config.js';
import { SUPPORTED_CHAT_COMPLETION_FIELDS, hasMultimodalContent, validateChatCompletionRequest, } from './schema.js';
import { ANTHROPIC_API_VERSION, executeAnthropicUpstreamCall, } from './anthropic-adapter.js';
import { DEFAULT_NIDO_MODEL, NIDO_CURATED_MODELS, executeNidoAccountUpstreamCall, fetchNidoAccountQuota, formatNidoPlanName, getNidoAccountProfilePath, isJwtFresh, loginNidoAccount, logoutNidoAccount, readNidoAccountProfile, refreshNidoAccountTokens, resolveNidoAccountProviderConfig, resolveNidoConfigDir, writeNidoAccountProfile, } from './nido-account-adapter.js';
import { probeLocalBackendReachability } from './live-probe.js';
import { renderControlPanelHtml } from './ui-html.js';
import { runDoctor } from '../doctor/doctor.js';
import { CodexAppServerWorker } from '../agent/codex-worker.js';
import { getRuntimeDiscoveryPath, readAgentsStatus, readRuntimeDiscovery, resolveNidoAiStateDir, writeRuntimeDiscovery, } from '../runtime-discovery.js';
import { formatSecretStoreLabel } from '../security/secret-store.js';
const MAX_TEXT_BODY_BYTES = 1_048_576; // 1 MiB for plain text/JSON requests
const MAX_MULTIMODAL_BODY_BYTES = 4_194_304; // 4 MiB for vision image_url requests
const LOOPBACK_HOST_RE = /^(?:127\.0\.0\.1|localhost|\[::1\])(?::\d{1,5})?$/i;
const LOOPBACK_ORIGIN_RE = /^http:\/\/(?:127\.0\.0\.1|localhost|\[::1\])(?::\d{1,5})?$/i;
const AUTH_FAIL_LIMIT_PER_MIN = 20;
const LOGIN_ATTEMPT_LIMIT_PER_MIN = 10;
const RATE_LIMIT_WINDOW_MS = 60_000;
const SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Cache-Control': 'no-store',
    'Referrer-Policy': 'no-referrer',
    'Cross-Origin-Resource-Policy': 'same-origin',
};
function isValidLoopbackHost(hostHeader, allowEmptyOnUds = false) {
    if (hostHeader === undefined || hostHeader === '') {
        return allowEmptyOnUds;
    }
    if (typeof hostHeader !== 'string') {
        return false;
    }
    return LOOPBACK_HOST_RE.test(hostHeader.trim());
}
function isValidLoopbackOrigin(originHeader) {
    if (originHeader === undefined) {
        return true;
    }
    if (typeof originHeader !== 'string') {
        return false;
    }
    return LOOPBACK_ORIGIN_RE.test(originHeader.trim());
}
function pruneRateTimestamps(arr, now = Date.now(), windowMs = RATE_LIMIT_WINDOW_MS) {
    while (arr.length > 0 && now - arr[0] > windowMs) {
        arr.shift();
    }
}
function isRateLimited(arr, limit, windowMs = RATE_LIMIT_WINDOW_MS) {
    pruneRateTimestamps(arr, Date.now(), windowMs);
    return arr.length >= limit;
}
function recordRateEvent(arr, windowMs = RATE_LIMIT_WINDOW_MS) {
    const now = Date.now();
    pruneRateTimestamps(arr, now, windowMs);
    arr.push(now);
}
class WorkspacePathDeniedError extends Error {
    statusCode = 403;
    constructor(message) {
        super(message);
    }
}
function isPathInsideOrEqual(targetPath, basePath) {
    const rel = path.relative(basePath, targetPath);
    return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}
function resolveRealPathWithMissingSuffix(targetPath) {
    const resolved = path.resolve(targetPath);
    let probe = resolved;
    const missingParts = [];
    while (!fs.existsSync(probe)) {
        const parent = path.dirname(probe);
        if (parent === probe) {
            break;
        }
        missingParts.unshift(path.basename(probe));
        probe = parent;
    }
    const realBase = fs.existsSync(probe) ? fs.realpathSync(probe) : probe;
    return path.join(realBase, ...missingParts);
}
function validateCodexWorkspaceCwd(requestedCwd, homeDir = os.homedir(), tmpDir = os.tmpdir()) {
    const realTarget = resolveRealPathWithMissingSuffix(requestedCwd);
    const realHome = fs.existsSync(homeDir) ? fs.realpathSync(homeDir) : path.resolve(homeDir);
    const realTmp = fs.existsSync(tmpDir) ? fs.realpathSync(tmpDir) : path.resolve(tmpDir);
    const allowedRoots = [realHome, realTmp];
    if (!allowedRoots.some((rootDir) => isPathInsideOrEqual(realTarget, rootDir))) {
        throw new WorkspacePathDeniedError(`Codex workspace path is outside user home/tmp: ${requestedCwd}`);
    }
    const xdgCfg = (process.env.XDG_CONFIG_HOME ?? '').trim() || path.join(realHome, '.config');
    const nidoAiDir = resolveNidoAiStateDir(realHome, process.env);
    const forbiddenSubdirs = [
        path.join(realHome, '.ssh'),
        path.join(realHome, '.gnupg'),
        path.join(realHome, '.aws'),
        path.join(realHome, '.kube'),
        resolveRealPathWithMissingSuffix(nidoAiDir),
        resolveRealPathWithMissingSuffix(path.join(xdgCfg, 'ubuntu-nido')),
    ];
    for (const blocked of forbiddenSubdirs) {
        if (isPathInsideOrEqual(realTarget, blocked)) {
            throw new WorkspacePathDeniedError(`Codex workspace path accesses protected credential directory: ${requestedCwd}`);
        }
    }
    return realTarget;
}
function logOperational(entry, silent = false) {
    if (silent)
        return;
    console.log(JSON.stringify(entry));
}
function sendJson(res, status, payload, extraHeaders = {}) {
    const body = JSON.stringify(payload);
    res.writeHead(status, {
        ...SECURITY_HEADERS,
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Length': Buffer.byteLength(body).toString(),
        ...extraHeaders,
    });
    if (res.req?.method === 'HEAD') {
        res.end();
    }
    else {
        res.end(body);
    }
}
function parseExplicitProviderPrefix(requestedModel) {
    if (!requestedModel)
        return { explicitKind: null, strippedModel: undefined };
    if (requestedModel.includes('/')) {
        const [prefix, rest] = requestedModel.split('/', 2);
        if (isChatBackendKind(prefix) && rest) {
            return { explicitKind: prefix, strippedModel: rest };
        }
    }
    return { explicitKind: null, strippedModel: requestedModel };
}
function resolveModelForBackend(requestedModel, backend) {
    const { strippedModel } = parseExplicitProviderPrefix(requestedModel);
    if (!strippedModel || strippedModel === 'auto' || strippedModel === 'default') {
        return backend.defaultModel;
    }
    return strippedModel;
}
function selectBackendsForRequest(reqData, config) {
    const { explicitKind, strippedModel } = parseExplicitProviderPrefix(reqData.model);
    // Rule 1: If client explicitly specified a provider prefix (e.g. "gemini/private-model"),
    // pin strictly to that provider and NEVER fail over to another backend.
    if (explicitKind !== null) {
        return {
            candidates: [config.backends[explicitKind]],
            allowFailover: false,
        };
    }
    // Rule 1b: Route Nido-exclusive model ("nido-llama") directly to the Nido Account backend
    // when configured, so NidoOffice Companion SSO works even if primaryBackend is gemini/openai/local.
    if (strippedModel === DEFAULT_NIDO_MODEL && config.backends.nido?.configured) {
        return {
            candidates: [config.backends.nido],
            allowFailover: false,
        };
    }
    const primaryKind = config.primaryBackend;
    const isGenericModelAlias = reqData.model === undefined || reqData.model === 'auto' || reqData.model === 'default';
    // Rule 2: Cross-provider failover is ONLY allowed when:
    // - the client requested a generic alias (omitted, "auto", or "default"), AND
    // - config.allowCrossProviderFailover is explicitly enabled, AND
    // - a distinct failoverBackend is configured.
    if (isGenericModelAlias &&
        config.allowCrossProviderFailover &&
        config.failoverBackend &&
        config.failoverBackend !== primaryKind) {
        const fb = config.backends[config.failoverBackend];
        if (fb.configured) {
            return {
                candidates: [config.backends[primaryKind], fb],
                allowFailover: true,
            };
        }
    }
    return {
        candidates: [config.backends[primaryKind]],
        allowFailover: false,
    };
}
function isRetriableUpstreamStatus(status) {
    // Never fail over on client errors, auth errors, billing errors, or model-not-found errors (400, 401, 402, 403, 404, 422).
    // Only fail over on rate-limit (429) or transient 5xx server/gateway errors.
    return status === 429 || status === 500 || status === 502 || status === 503 || status === 504;
}
class PayloadTooLargeError extends Error {
    statusCode = 413;
    constructor(maxBytes) {
        super(`Request body exceeds ${maxBytes} byte limit`);
    }
}
async function readRequestBody(req, maxBytes = MAX_TEXT_BODY_BYTES) {
    return new Promise((resolve, reject) => {
        const chunks = [];
        let total = 0;
        let settled = false;
        req.on('data', (chunk) => {
            if (settled)
                return;
            total += chunk.length;
            if (total > maxBytes) {
                settled = true;
                reject(new PayloadTooLargeError(maxBytes));
                req.destroy();
                return;
            }
            chunks.push(chunk);
        });
        req.on('end', () => {
            if (settled)
                return;
            settled = true;
            resolve({ text: Buffer.concat(chunks).toString('utf8'), byteLength: total });
        });
        req.on('error', (err) => {
            if (settled)
                return;
            settled = true;
            reject(err);
        });
    });
}
function timingSafeBearerMatch(authHeader, expectedToken) {
    if (!expectedToken || expectedToken.length < MIN_CLIENT_TOKEN_LENGTH)
        return false;
    if (!authHeader || !authHeader.startsWith('Bearer '))
        return false;
    const provided = authHeader.slice('Bearer '.length).trim();
    const a = Buffer.from(provided, 'utf8');
    const b = Buffer.from(expectedToken, 'utf8');
    if (a.length !== b.length)
        return false;
    return crypto.timingSafeEqual(a, b);
}
/**
 * Requests a stream from the upstream OpenAI-compatible endpoint so we can:
 * 1. Accurately measure TTFT (Time To First Token) for both stream: true and stream: false.
 * 2. Verify terminal stream completion (both a terminal finish_reason AND data: [DONE])
 *    before marking a response successful or emitting [DONE] to the client.
 */
async function executeUpstreamCall(backend, reqData, wantClientStream, res, requestId, requestStartMs, nidoExecOpts = {}) {
    const model = resolveModelForBackend(reqData.model, backend);
    if (backend.kind === 'nido') {
        return executeNidoAccountUpstreamCall({
            backend,
            model,
            reqData,
            wantClientStream,
            res,
            requestId,
            requestStartMs,
            securityHeaders: SECURITY_HEADERS,
            ...nidoExecOpts,
        });
    }
    if (backend.kind === 'anthropic') {
        return executeAnthropicUpstreamCall({
            backend,
            model,
            reqData,
            wantClientStream,
            res,
            requestId,
            requestStartMs,
            securityHeaders: SECURITY_HEADERS,
        });
    }
    if (!backend.configured) {
        return {
            ok: false,
            emittedStreamBytes: false,
            retriableForFailover: true,
            status: 503,
            model,
            ttftMs: 0,
            totalMs: Math.round(performance.now() - requestStartMs),
            errorPayload: {
                error: {
                    type: 'backend_not_configured',
                    message: `Backend '${backend.kind}' is not configured.`,
                },
            },
        };
    }
    const upstreamPayload = {
        model,
        messages: reqData.messages,
        stream: true,
    };
    if (reqData.temperature !== undefined)
        upstreamPayload.temperature = reqData.temperature;
    if (reqData.max_tokens !== undefined)
        upstreamPayload.max_tokens = reqData.max_tokens;
    if (reqData.max_completion_tokens !== undefined) {
        upstreamPayload.max_completion_tokens = reqData.max_completion_tokens;
    }
    if (reqData.top_p !== undefined)
        upstreamPayload.top_p = reqData.top_p;
    if (reqData.stop !== undefined)
        upstreamPayload.stop = reqData.stop;
    if (reqData.response_format !== undefined) {
        upstreamPayload.response_format = reqData.response_format;
    }
    const callStartMs = performance.now();
    let upstreamRes;
    try {
        upstreamRes = await fetch(`${backend.baseUrl}/chat/completions`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${backend.apiKey}`,
            },
            body: JSON.stringify(upstreamPayload),
            signal: AbortSignal.timeout(120_000),
        });
    }
    catch (err) {
        return {
            ok: false,
            emittedStreamBytes: false,
            retriableForFailover: true,
            status: 502,
            model,
            ttftMs: 0,
            totalMs: Math.round(performance.now() - requestStartMs),
            errorPayload: {
                error: {
                    type: 'upstream_connection_error',
                    message: err instanceof Error ? err.message : 'Upstream connection failed',
                },
            },
        };
    }
    if (!upstreamRes.ok || !upstreamRes.body) {
        let errBody = { error: { message: `Upstream HTTP ${upstreamRes.status}` } };
        try {
            errBody = await upstreamRes.json();
        }
        catch {
            // ignore json parse error
        }
        return {
            ok: false,
            emittedStreamBytes: false,
            retriableForFailover: isRetriableUpstreamStatus(upstreamRes.status),
            status: upstreamRes.status,
            model,
            ttftMs: 0,
            totalMs: Math.round(performance.now() - requestStartMs),
            errorPayload: errBody,
        };
    }
    const reader = upstreamRes.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let ttftMs = null;
    let emittedStreamBytes = false;
    let aggregatedText = '';
    let finishReason = null;
    let sawFinishReason = false;
    let sawDoneMarker = false;
    let sawAnyChoiceChunk = false;
    let responseId = `chatcmpl-${requestId}`;
    const processLine = (rawLine) => {
        const trimmed = rawLine.trim();
        if (!trimmed.startsWith('data:'))
            return;
        const dataStr = trimmed.slice(5).trim();
        if (dataStr === '[DONE]') {
            sawDoneMarker = true;
            return;
        }
        let parsed;
        try {
            parsed = JSON.parse(dataStr);
        }
        catch {
            return;
        }
        if (typeof parsed.id === 'string')
            responseId = parsed.id;
        if (!Array.isArray(parsed.choices) || parsed.choices.length === 0)
            return;
        sawAnyChoiceChunk = true;
        const firstChoice = (parsed.choices[0] ?? {});
        const delta = (firstChoice.delta ?? {});
        const deltaContent = typeof delta.content === 'string' ? delta.content : '';
        if (deltaContent.length > 0 && ttftMs === null) {
            ttftMs = Math.round(performance.now() - callStartMs);
        }
        if (typeof firstChoice.finish_reason === 'string' &&
            firstChoice.finish_reason.trim().length > 0) {
            finishReason = firstChoice.finish_reason.trim();
            sawFinishReason = true;
        }
        if (wantClientStream) {
            if (!emittedStreamBytes) {
                res.writeHead(200, {
                    ...SECURITY_HEADERS,
                    'Content-Type': 'text/event-stream; charset=utf-8',
                    'Cache-Control': 'no-cache',
                    Connection: 'keep-alive',
                    'x-nidoai-request-id': requestId,
                    'x-nidoai-backend': backend.kind,
                    'x-nidoai-model': model,
                });
                emittedStreamBytes = true;
            }
            res.write(`data: ${JSON.stringify(parsed)}\n\n`);
        }
        else {
            aggregatedText += deltaContent;
        }
    };
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                buffer += decoder.decode();
                if (buffer.trim().length > 0) {
                    for (const remLine of buffer.split(/\r?\n/)) {
                        processLine(remLine);
                    }
                    buffer = '';
                }
                break;
            }
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split(/\r?\n/);
            buffer = lines.pop() ?? '';
            for (const line of lines) {
                processLine(line);
            }
        }
    }
    catch (streamErr) {
        if (emittedStreamBytes) {
            // Never emit [DONE] on an interrupted stream; send an explicit error event and close.
            res.write(`event: error\ndata: ${JSON.stringify({
                error: {
                    type: 'upstream_stream_error',
                    code: 'incomplete_upstream_response',
                    message: streamErr instanceof Error ? streamErr.message : 'Upstream stream interrupted',
                },
            })}\n\n`);
            res.end();
            return {
                ok: false,
                emittedStreamBytes: true,
                retriableForFailover: false,
                status: 502,
                model,
                ttftMs: ttftMs ?? 0,
                totalMs: Math.round(performance.now() - requestStartMs),
            };
        }
        return {
            ok: false,
            emittedStreamBytes: false,
            retriableForFailover: true,
            status: 502,
            model,
            ttftMs: ttftMs ?? 0,
            totalMs: Math.round(performance.now() - requestStartMs),
            errorPayload: {
                error: {
                    type: 'upstream_stream_error',
                    code: 'incomplete_upstream_response',
                    message: streamErr instanceof Error ? streamErr.message : 'Stream interrupted',
                },
            },
        };
    }
    // Strict terminal completion check:
    // Upstream MUST have sent at least one valid choice chunk, a non-empty finish_reason, AND data: [DONE].
    const isCompleteStream = sawAnyChoiceChunk && sawFinishReason && sawDoneMarker;
    const finalTtftMs = ttftMs ?? Math.round(performance.now() - callStartMs);
    const totalMs = Math.round(performance.now() - requestStartMs);
    if (!isCompleteStream) {
        const incompleteErrorPayload = {
            error: {
                type: 'upstream_incomplete_stream',
                code: 'incomplete_upstream_response',
                message: `Upstream stream ended prematurely without completion signals (sawChoice=${String(sawAnyChoiceChunk)}, sawFinishReason=${String(sawFinishReason)}, sawDone=${String(sawDoneMarker)}).`,
            },
        };
        if (emittedStreamBytes) {
            // Stream headers were already sent to SSE client; emit an error event and do NOT send data: [DONE]!
            res.write(`event: error\ndata: ${JSON.stringify(incompleteErrorPayload)}\n\n`);
            res.end();
            return {
                ok: false,
                emittedStreamBytes: true,
                retriableForFailover: false,
                status: 502,
                model,
                ttftMs: finalTtftMs,
                totalMs,
                errorPayload: incompleteErrorPayload,
            };
        }
        return {
            ok: false,
            emittedStreamBytes: false,
            retriableForFailover: true,
            status: 502,
            model,
            ttftMs: finalTtftMs,
            totalMs,
            errorPayload: incompleteErrorPayload,
        };
    }
    if (wantClientStream) {
        res.write('data: [DONE]\n\n');
        res.end();
    }
    else {
        sendJson(res, 200, {
            id: responseId,
            object: 'chat.completion',
            created: Math.floor(Date.now() / 1000),
            model,
            choices: [
                {
                    index: 0,
                    message: {
                        role: 'assistant',
                        content: aggregatedText,
                    },
                    finish_reason: finishReason,
                },
            ],
        }, {
            'x-nidoai-request-id': requestId,
            'x-nidoai-backend': backend.kind,
            'x-nidoai-model': model,
            'x-nidoai-ttft-ms': String(finalTtftMs),
            'x-nidoai-total-ms': String(totalMs),
        });
    }
    return {
        ok: true,
        emittedStreamBytes,
        retriableForFailover: false,
        status: 200,
        model,
        ttftMs: finalTtftMs,
        totalMs,
    };
}
function parseCookies(cookieHeader) {
    if (!cookieHeader)
        return {};
    const out = {};
    for (const part of cookieHeader.split(';')) {
        const eq = part.indexOf('=');
        if (eq <= 0)
            continue;
        const k = part.slice(0, eq).trim();
        const v = part.slice(eq + 1).trim();
        out[k] = v;
    }
    return out;
}
function timingSafeStringEqual(aStr, bStr) {
    const a = Buffer.from(aStr, 'utf8');
    const b = Buffer.from(bStr, 'utf8');
    if (a.length !== b.length)
        return false;
    return crypto.timingSafeEqual(a, b);
}
function verifyAdminRequestAuth(req, expectedClientToken, expectedCsrfToken, allowCsrfCookie = true) {
    if (timingSafeBearerMatch(req.headers.authorization, expectedClientToken)) {
        return true;
    }
    if (!allowCsrfCookie) {
        return false;
    }
    const secFetchSite = req.headers['sec-fetch-site'];
    if (typeof secFetchSite === 'string' && secFetchSite === 'cross-site') {
        return false;
    }
    const headerCsrf = req.headers['x-nidoai-csrf'];
    const cookies = parseCookies(req.headers.cookie);
    const cookieCsrf = cookies['nidoai_ui_csrf'];
    if (typeof headerCsrf === 'string' &&
        typeof cookieCsrf === 'string' &&
        timingSafeStringEqual(headerCsrf, expectedCsrfToken) &&
        timingSafeStringEqual(cookieCsrf, expectedCsrfToken)) {
        return true;
    }
    return false;
}
export async function startGatewayServer(initialConfig, options = {}) {
    if (!initialConfig.clientToken || initialConfig.clientToken.length < MIN_CLIENT_TOKEN_LENGTH) {
        throw new Error(`NIDOAI_CLIENT_TOKEN must be explicitly configured (minimum ${MIN_CLIENT_TOKEN_LENGTH} characters) before starting the gateway server.`);
    }
    let currentConfig = { ...initialConfig };
    let activeClientToken = initialConfig.clientToken;
    let actualPort = initialConfig.port;
    let activeSocketPath = null;
    const ephemeralClientToken = Boolean(options.ephemeralClientToken);
    const stateDir = options.stateDir || initialConfig.stateDir || resolveNidoAiStateDir();
    const configDir = options.configDir || initialConfig.configDir || resolveNidoConfigDir();
    const useSecretStore = process.env.NIDOAI_SECRET_STORE !== 'file';
    const secretStoreOptions = { env: process.env };
    const envLocalPath = path.join(stateDir, '.env.local');
    const accountProfilePath = getNidoAccountProfilePath(configDir);
    const statMtimeMs = (filePath) => {
        try {
            return fs.statSync(filePath).mtimeMs;
        }
        catch {
            return -1;
        }
    };
    let lastEnvMtimeMs = statMtimeMs(envLocalPath);
    let lastAccountMtimeMs = statMtimeMs(accountProfilePath);
    let lastKeyringRetryAt = 0;
    const reloadCurrentConfig = () => {
        currentConfig = loadConfig({ cwd: stateDir, useSecretStore });
        if (activeClientToken && activeClientToken.length >= MIN_CLIENT_TOKEN_LENGTH) {
            currentConfig.clientToken = activeClientToken;
            currentConfig.clientTokenConfigured = true;
        }
        lastEnvMtimeMs = statMtimeMs(envLocalPath);
        lastAccountMtimeMs = statMtimeMs(accountProfilePath);
    };
    const ensureFreshConfig = () => {
        const envMtime = statMtimeMs(envLocalPath);
        const acctMtime = statMtimeMs(accountProfilePath);
        const mtimeChanged = envMtime !== lastEnvMtimeMs || acctMtime !== lastAccountMtimeMs;
        const now = Date.now();
        const needKeyringRetry = !mtimeChanged &&
            acctMtime > 0 &&
            !currentConfig.backends?.nido?.configured &&
            now - lastKeyringRetryAt >= 3000;
        if (!mtimeChanged && !needKeyringRetry) {
            return;
        }
        if (needKeyringRetry) {
            lastKeyringRetryAt = now;
        }
        const prevPrimary = currentConfig.primaryBackend;
        const prevPrimaryCfg = Boolean(currentConfig.backends?.[prevPrimary]?.configured);
        const prevNidoCfg = Boolean(currentConfig.backends?.nido?.configured);
        reloadCurrentConfig();
        const nextPrimary = currentConfig.primaryBackend;
        const nextPrimaryCfg = Boolean(currentConfig.backends?.[nextPrimary]?.configured);
        const nextNidoCfg = Boolean(currentConfig.backends?.nido?.configured);
        if (prevPrimary !== nextPrimary || prevPrimaryCfg !== nextPrimaryCfg || prevNidoCfg !== nextNidoCfg) {
            void publishRuntimeDiscoveryNow();
        }
    };
    const buildSanitizedAccountPayload = () => {
        const profile = readNidoAccountProfile(configDir);
        const nidoBackend = currentConfig.backends?.nido;
        return {
            signedIn: Boolean(profile?.signedIn || nidoBackend?.configured),
            backendConfigured: Boolean(nidoBackend?.configured),
            tokenFresh: isJwtFresh(nidoBackend?.apiKey || ''),
            account: profile,
            userId: profile?.userId || '',
            email: profile?.email || '',
            fullName: profile?.fullName || '',
            planCode: profile?.planCode || 'free',
            planName: profile?.planName || formatNidoPlanName(profile?.planCode || 'free', Boolean(profile?.isPro)),
            isPro: Boolean(profile?.isPro),
            source: profile?.source || 'nido',
            quota: profile?.quota || null,
            sync: profile?.sync || { enabled: false, lastSyncAt: '', items: ['vielotus', 'nidobox', 'ai-policy'] },
            updatedAt: profile?.updatedAt || '',
        };
    };
    const buildHealthNidoSummary = () => {
        const acct = buildSanitizedAccountPayload();
        return {
            signedIn: acct.signedIn,
            planName: acct.planName,
            isPro: acct.isPro,
            aiCreditsRemaining: acct.quota?.aiCreditsRemaining ?? null,
            aiCreditsQuota: acct.quota?.aiCreditsQuota ?? null,
        };
    };
    const publishRuntimeDiscoveryNow = async () => {
        if (!options.writeRuntimeDiscovery)
            return;
        const p = currentConfig.backends[currentConfig.primaryBackend];
        const lp = p.kind === 'local' && p.configured
            ? await probeLocalBackendReachability(p.baseUrl, { apiKey: p.apiKey, force: true })
            : null;
        writeRuntimeDiscovery({
            baseUrl: `http://127.0.0.1:${actualPort}`,
            clientToken: currentConfig.clientToken,
            ...(activeSocketPath ? { socketPath: activeSocketPath } : {}),
            capabilities: {
                vision: p.kind !== 'nido',
                streaming: true,
                jsonObject: true,
                primaryBackend: p.kind,
                primaryConfigured: p.configured,
                primaryReachable: lp ? lp.reachable : p.configured,
                secretStoreMode: currentConfig.secretStoreMode,
            },
        });
    };
    const syncNidoAccountNow = async () => {
        const nidoBackend = currentConfig.backends?.nido;
        if (!nidoBackend?.configured) {
            return { ok: false, status: 401, error: 'Chưa đăng nhập Tài khoản Nido.', payload: buildSanitizedAccountPayload() };
        }
        const providerCfg = resolveNidoAccountProviderConfig(process.env, configDir);
        let accessToken = nidoBackend.apiKey || '';
        const refreshToken = nidoBackend.refreshToken || '';
        if ((!accessToken || !isJwtFresh(accessToken)) && refreshToken) {
            const rot = await refreshNidoAccountTokens({
                refreshToken,
                providerConfig: providerCfg,
                stateDir,
                useSecretStore,
                secretStoreOptions,
            });
            if (rot.ok && rot.accessToken) {
                accessToken = rot.accessToken;
                reloadCurrentConfig();
            }
            else if (rot.revoked) {
                await logoutNidoAccount({
                    accessToken: '',
                    providerConfig: providerCfg,
                    stateDir,
                    configDir,
                    secretStoreOptions,
                });
                reloadCurrentConfig();
                return { ok: false, status: 401, error: rot.error || 'Phiên đăng nhập Tài khoản Nido đã hết hạn.', payload: buildSanitizedAccountPayload() };
            }
        }
        if (!accessToken) {
            return { ok: false, status: 401, error: 'Thiếu access token Tài khoản Nido.', payload: buildSanitizedAccountPayload() };
        }
        let quotaRes = await fetchNidoAccountQuota({ accessToken, providerConfig: providerCfg });
        if (!quotaRes.ok && quotaRes.status === 401 && refreshToken) {
            const rot = await refreshNidoAccountTokens({
                refreshToken,
                providerConfig: providerCfg,
                stateDir,
                useSecretStore,
                secretStoreOptions,
            });
            if (rot.ok && rot.accessToken) {
                accessToken = rot.accessToken;
                reloadCurrentConfig();
                quotaRes = await fetchNidoAccountQuota({ accessToken, providerConfig: providerCfg });
            }
        }
        if (quotaRes.ok) {
            const prev = readNidoAccountProfile(configDir);
            writeNidoAccountProfile({
                ...(prev || {}),
                schemaVersion: 1,
                signedIn: true,
                userId: quotaRes.userId || prev?.userId || '',
                email: prev?.email || '',
                fullName: prev?.fullName || '',
                planCode: quotaRes.planCode || prev?.planCode || 'free',
                planName: formatNidoPlanName(quotaRes.planCode || prev?.planCode || 'free', quotaRes.isPro),
                isPro: quotaRes.isPro,
                source: quotaRes.source || prev?.source || 'nido',
                ...(quotaRes.quota ? { quota: quotaRes.quota } : {}),
                sync: prev?.sync || { enabled: false, lastSyncAt: '', items: ['vielotus', 'nidobox', 'ai-policy'] },
                updatedAt: new Date().toISOString(),
            }, configDir);
        }
        return {
            ok: quotaRes.ok,
            status: quotaRes.ok ? 200 : (quotaRes.status || 502),
            ...(quotaRes.ok ? {} : { error: quotaRes.error || 'Không thể đồng bộ Tài khoản Nido.' }),
            payload: buildSanitizedAccountPayload(),
        };
    };
    const csrfToken = crypto.randomUUID();
    const enableWebUi = ['1', 'true', 'yes'].includes(String(process.env.NIDOAI_ENABLE_WEB_UI ?? '').trim().toLowerCase());
    const cspNonce = enableWebUi ? crypto.randomBytes(16).toString('base64') : '';
    const authFailTimestamps = [];
    const loginAttemptTimestamps = [];
    const recentLogs = [];
    let codexWorker = null;
    let codexWorkerCwd = os.homedir();
    let activeCodexThreadId = null;
    const recordLog = (entry) => {
        recentLogs.unshift(entry);
        if (recentLogs.length > 50)
            recentLogs.pop();
        logOperational(entry, options.silent);
    };
    let codexWorkerQueue = Promise.resolve();
    const ensureCodexWorker = async (requestedCwd) => {
        let release;
        const prev = codexWorkerQueue;
        codexWorkerQueue = new Promise((r) => {
            release = r;
        });
        await prev;
        try {
            const targetCwd = typeof requestedCwd === 'string' && requestedCwd.trim().length > 0
                ? validateCodexWorkspaceCwd(requestedCwd.trim())
                : codexWorkerCwd;
            fs.mkdirSync(targetCwd, { recursive: true });
            if (!codexWorker || !codexWorker.isRunning() || codexWorkerCwd !== targetCwd) {
                if (codexWorker) {
                    await codexWorker.stop();
                }
                codexWorkerCwd = targetCwd;
                codexWorker = new CodexAppServerWorker({
                    codexBin: currentConfig.codexBin,
                    cwd: targetCwd,
                    sandboxMode: 'read-only',
                    approvalPolicy: 'never',
                });
                await codexWorker.start();
            }
            return codexWorker;
        }
        finally {
            release();
        }
    };
    const cachedHtml = enableWebUi ? renderControlPanelHtml(csrfToken, PKG_VERSION, cspNonce) : '';
    const cachedHtmlBytes = Buffer.byteLength(cachedHtml).toString();
    const handleHttpRequest = async (req, res) => {
        const requestStartMs = performance.now();
        const requestId = crypto.randomUUID();
        const method = req.method ?? 'GET';
        const isUdsSocket = !req.socket?.remoteAddress;
        if (!isValidLoopbackHost(req.headers.host, isUdsSocket)) {
            sendJson(res, 403, {
                error: {
                    type: 'security_error',
                    code: 'invalid_host_header',
                    message: 'Rejected non-loopback Host header.',
                },
            });
            return;
        }
        if (!isValidLoopbackOrigin(req.headers.origin)) {
            sendJson(res, 403, {
                error: {
                    type: 'security_error',
                    code: 'invalid_origin',
                    message: 'Rejected cross-origin browser request.',
                },
            });
            return;
        }
        const url = new URL(req.url ?? '/', `http://${currentConfig.host}:${actualPort}`);
        const pathname = url.pathname;
        // 0. GET / (Interactive Control Panel — disabled by default unless NIDOAI_ENABLE_WEB_UI=1)
        if ((method === 'GET' || method === 'HEAD') && pathname === '/') {
            const totalMs = Math.round(performance.now() - requestStartMs);
            if (!enableWebUi) {
                sendJson(res, 404, {
                    error: {
                        type: 'not_found',
                        code: 'web_ui_disabled',
                        message: 'NidoAI Web Control Panel is disabled by default for security. Use NidoHub or set NIDOAI_ENABLE_WEB_UI=1.',
                    },
                });
                recordLog({
                    timestamp: new Date().toISOString(),
                    requestId,
                    method,
                    path: pathname,
                    status: 404,
                    totalMs,
                });
                return;
            }
            res.writeHead(200, {
                ...SECURITY_HEADERS,
                'Content-Security-Policy': `default-src 'none'; style-src 'nonce-${cspNonce}'; script-src 'nonce-${cspNonce}'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`,
                'Content-Type': 'text/html; charset=utf-8',
                'Content-Length': cachedHtmlBytes,
                'Set-Cookie': `nidoai_ui_csrf=${csrfToken}; HttpOnly; SameSite=Strict; Path=/`,
            });
            if (method !== 'HEAD')
                res.end(cachedHtml);
            else
                res.end();
            recordLog({
                timestamp: new Date().toISOString(),
                requestId,
                method,
                path: pathname,
                status: 200,
                totalMs,
            });
            return;
        }
        // 0b. Admin Control Plane API (/api/admin/*)
        if (pathname.startsWith('/api/admin/')) {
            if (isRateLimited(authFailTimestamps, AUTH_FAIL_LIMIT_PER_MIN)) {
                sendJson(res, 429, {
                    error: {
                        type: 'rate_limit_error',
                        code: 'rate_limited',
                        message: 'Too many authentication failures. Try again later.',
                    },
                }, { 'Retry-After': '60' });
                return;
            }
            if (!verifyAdminRequestAuth(req, currentConfig.clientToken, csrfToken, enableWebUi)) {
                recordRateEvent(authFailTimestamps);
                sendJson(res, 401, {
                    error: {
                        type: 'authentication_error',
                        code: 'unauthorized_admin_request',
                        message: 'Missing valid CSRF session or Bearer token for Admin API.',
                    },
                });
                return;
            }
            if (method === 'GET' && pathname === '/api/admin/pairing-token') {
                sendJson(res, 403, {
                    error: {
                        type: 'security_error',
                        code: 'endpoint_disabled',
                        message: 'Raw token export over HTTP is disabled. Read ~/.nidoai/runtime.json directly.',
                    },
                });
                return;
            }
            if (method === 'GET' && pathname === '/api/admin/state') {
                ensureFreshConfig();
                const primary = currentConfig.backends[currentConfig.primaryBackend];
                const localProbe = currentConfig.backends.local.configured
                    ? await probeLocalBackendReachability(currentConfig.backends.local.baseUrl, {
                        apiKey: currentConfig.backends.local.apiKey,
                    })
                    : { reachable: false, latencyMs: 0, models: [], checkedAt: Date.now() };
                const primaryReachable = primary.kind === 'local' ? primary.configured && localProbe.reachable : primary.configured;
                const secretStoreMode = currentConfig.secretStoreMode ?? 'file-0600-fallback';
                sendJson(res, 200, {
                    bind: `${currentConfig.host}:${actualPort}`,
                    discoveryPath: getRuntimeDiscoveryPath(),
                    maskedClientToken: maskSecret(currentConfig.clientToken),
                    secretStore: {
                        mode: secretStoreMode,
                        label: formatSecretStoreLabel(secretStoreMode),
                    },
                    primaryBackend: {
                        kind: primary.kind,
                        configured: primary.configured,
                        reachable: primaryReachable,
                        defaultModel: primary.defaultModel,
                    },
                    failoverBackend: currentConfig.allowCrossProviderFailover
                        ? currentConfig.failoverBackend
                        : null,
                    allowCrossProviderFailover: currentConfig.allowCrossProviderFailover,
                    backends: {
                        nido: {
                            configured: currentConfig.backends.nido.configured,
                            reachable: currentConfig.backends.nido.configured,
                            defaultModel: currentConfig.backends.nido.defaultModel,
                            baseUrl: currentConfig.backends.nido.baseUrl,
                            maskedKey: maskSecret(currentConfig.backends.nido.apiKey),
                        },
                        gemini: {
                            configured: currentConfig.backends.gemini.configured,
                            reachable: currentConfig.backends.gemini.configured,
                            defaultModel: currentConfig.backends.gemini.defaultModel,
                            baseUrl: currentConfig.backends.gemini.baseUrl,
                            maskedKey: maskSecret(currentConfig.backends.gemini.apiKey),
                        },
                        openai: {
                            configured: currentConfig.backends.openai.configured,
                            reachable: currentConfig.backends.openai.configured,
                            defaultModel: currentConfig.backends.openai.defaultModel,
                            baseUrl: currentConfig.backends.openai.baseUrl,
                            maskedKey: maskSecret(currentConfig.backends.openai.apiKey),
                        },
                        anthropic: {
                            configured: currentConfig.backends.anthropic.configured,
                            reachable: currentConfig.backends.anthropic.configured,
                            defaultModel: currentConfig.backends.anthropic.defaultModel,
                            baseUrl: currentConfig.backends.anthropic.baseUrl,
                            maskedKey: maskSecret(currentConfig.backends.anthropic.apiKey),
                        },
                        local: {
                            configured: currentConfig.backends.local.configured,
                            reachable: localProbe.reachable,
                            defaultModel: currentConfig.backends.local.defaultModel,
                            baseUrl: currentConfig.backends.local.baseUrl,
                            maskedKey: '',
                        },
                    },
                    nidoAccount: buildSanitizedAccountPayload(),
                    agentsStatus: readAgentsStatus(),
                    codex: {
                        running: codexWorker ? codexWorker.isRunning() : false,
                        activeThreadId: activeCodexThreadId,
                        cwd: codexWorkerCwd,
                    },
                    logs: recentLogs,
                });
                return;
            }
            if (method === 'POST' && pathname === '/api/admin/doctor') {
                const checks = await runDoctor(currentConfig);
                sendJson(res, 200, { checks });
                return;
            }
            if (method === 'POST' && pathname === '/api/admin/nido-account/login') {
                if (isRateLimited(loginAttemptTimestamps, LOGIN_ATTEMPT_LIMIT_PER_MIN)) {
                    sendJson(res, 429, {
                        ok: false,
                        error: {
                            type: 'rate_limit_error',
                            code: 'rate_limited',
                            message: 'Too many login attempts. Please wait a minute before retrying.',
                        },
                    }, { 'Retry-After': '60' });
                    return;
                }
                recordRateEvent(loginAttemptTimestamps);
                try {
                    const { text: raw } = await readRequestBody(req);
                    const body = JSON.parse(raw || '{}');
                    const providerCfg = resolveNidoAccountProviderConfig(process.env, configDir);
                    const loginRes = await loginNidoAccount({
                        email: body.email,
                        password: body.password,
                        providerConfig: providerCfg,
                        stateDir,
                        configDir,
                        useSecretStore,
                        secretStoreOptions,
                        setAsPrimary: body.setAsPrimary !== false,
                    });
                    if (!loginRes.ok) {
                        sendJson(res, loginRes.status || 401, {
                            ok: false,
                            error: { message: loginRes.error || 'Đăng nhập thất bại.' },
                        });
                        return;
                    }
                    reloadCurrentConfig();
                    await publishRuntimeDiscoveryNow();
                    sendJson(res, 200, { ok: true, account: loginRes.profile });
                }
                catch (err) {
                    sendJson(res, 400, {
                        ok: false,
                        error: { message: err instanceof Error ? err.message : 'Invalid Nido Account login request' },
                    });
                }
                return;
            }
            if (method === 'POST' && pathname === '/api/admin/nido-account/logout') {
                try {
                    const providerCfg = resolveNidoAccountProviderConfig(process.env, configDir);
                    const nidoBackend = currentConfig.backends?.nido;
                    await logoutNidoAccount({
                        accessToken: nidoBackend?.apiKey || '',
                        providerConfig: providerCfg,
                        stateDir,
                        configDir,
                        secretStoreOptions,
                    });
                    if (currentConfig.primaryBackend === 'nido') {
                        saveConfigUpdatesToEnvLocal({ primaryBackend: 'gemini', clearNidoAccountTokens: true }, stateDir, { useSecretStore, secretStoreOptions });
                    }
                    reloadCurrentConfig();
                    await publishRuntimeDiscoveryNow();
                    sendJson(res, 200, { ok: true });
                }
                catch (err) {
                    sendJson(res, 500, {
                        ok: false,
                        error: { message: err instanceof Error ? err.message : 'Logout failed' },
                    });
                }
                return;
            }
            if (method === 'POST' && pathname === '/api/admin/config') {
                try {
                    const { text: raw } = await readRequestBody(req);
                    const body = JSON.parse(raw || '{}');
                    saveConfigUpdatesToEnvLocal(body, stateDir, { useSecretStore, secretStoreOptions });
                    reloadCurrentConfig();
                    await publishRuntimeDiscoveryNow();
                    sendJson(res, 200, { ok: true, secretStoreMode: currentConfig.secretStoreMode });
                }
                catch (err) {
                    sendJson(res, 400, {
                        error: { message: err instanceof Error ? err.message : 'Invalid config update' },
                    });
                }
                return;
            }
            if (method === 'POST' && pathname === '/api/admin/rotate-token') {
                const newToken = `nidoai_${crypto.randomBytes(24).toString('hex')}`;
                activeClientToken = newToken;
                if (!ephemeralClientToken) {
                    saveConfigUpdatesToEnvLocal({ clientToken: newToken }, stateDir, { useSecretStore, secretStoreOptions });
                    reloadCurrentConfig();
                }
                else {
                    currentConfig.clientToken = newToken;
                    currentConfig.clientTokenConfigured = true;
                }
                await publishRuntimeDiscoveryNow();
                sendJson(res, 200, { ok: true, rotated: true });
                return;
            }
            if (method === 'POST' && pathname === '/api/admin/playground/chat') {
                try {
                    const { text: raw } = await readRequestBody(req);
                    const body = JSON.parse(raw || '{}');
                    const chatRes = await fetch(`http://127.0.0.1:${actualPort}/v1/chat/completions`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            Authorization: `Bearer ${currentConfig.clientToken}`,
                        },
                        signal: AbortSignal.timeout(60_000),
                        body: JSON.stringify({
                            model: body.model || 'auto',
                            messages: [{ role: 'user', content: body.prompt || 'Hello' }],
                            ...(body.responseFormat === 'json_object'
                                ? { response_format: { type: 'json_object' } }
                                : {}),
                        }),
                    });
                    const data = (await chatRes.json());
                    if (!chatRes.ok) {
                        sendJson(res, chatRes.status, data);
                        return;
                    }
                    const choices = Array.isArray(data.choices) ? data.choices : [];
                    const firstChoice = (choices[0] ?? {});
                    const message = (firstChoice.message ?? {});
                    sendJson(res, 200, {
                        content: typeof message.content === 'string' ? message.content : JSON.stringify(data),
                        backend: chatRes.headers.get('x-nidoai-backend') ?? 'unknown',
                        model: chatRes.headers.get('x-nidoai-model') ?? (body.model || 'auto'),
                        ttftMs: Number(chatRes.headers.get('x-nidoai-ttft-ms') ?? 0),
                        totalMs: Number(chatRes.headers.get('x-nidoai-total-ms') ?? 0),
                    });
                }
                catch (err) {
                    sendJson(res, 500, {
                        error: { message: err instanceof Error ? err.message : 'Playground call failed' },
                    });
                }
                return;
            }
            if (method === 'POST' && pathname === '/api/admin/codex/thread/start') {
                try {
                    const { text: raw } = await readRequestBody(req);
                    const body = JSON.parse(raw || '{}');
                    const worker = await ensureCodexWorker(body.cwd);
                    const thread = await worker.startThread({ cwd: codexWorkerCwd });
                    activeCodexThreadId = thread.threadId;
                    sendJson(res, 200, {
                        threadId: thread.threadId,
                        cwd: thread.cwd,
                        handshakeMs: worker.handshakeMs,
                    });
                }
                catch (err) {
                    const status = err instanceof WorkspacePathDeniedError ? err.statusCode : 500;
                    sendJson(res, status, {
                        error: { message: err instanceof Error ? err.message : 'Codex startThread failed' },
                    });
                }
                return;
            }
            if (method === 'POST' && pathname === '/api/admin/codex/thread/resume') {
                try {
                    const { text: raw } = await readRequestBody(req);
                    const body = JSON.parse(raw || '{}');
                    if (!body.threadId || !body.threadId.trim()) {
                        sendJson(res, 400, { error: { message: 'threadId is required to resume a session.' } });
                        return;
                    }
                    const worker = await ensureCodexWorker(body.cwd);
                    const resumed = await worker.resumeThread({
                        threadId: body.threadId.trim(),
                        cwd: codexWorkerCwd,
                    });
                    activeCodexThreadId = resumed.threadId;
                    sendJson(res, 200, {
                        threadId: resumed.threadId,
                        cwd: resumed.cwd,
                    });
                }
                catch (err) {
                    const status = err instanceof WorkspacePathDeniedError ? err.statusCode : 500;
                    sendJson(res, status, {
                        error: { message: err instanceof Error ? err.message : 'Codex resumeThread failed' },
                    });
                }
                return;
            }
            if (method === 'POST' && pathname === '/api/admin/codex/turn/run') {
                try {
                    const { text: raw } = await readRequestBody(req);
                    const body = JSON.parse(raw || '{}');
                    const worker = await ensureCodexWorker(body.cwd);
                    let targetThreadId = body.threadId?.trim() || activeCodexThreadId;
                    if (!targetThreadId) {
                        const started = await worker.startThread({ cwd: codexWorkerCwd });
                        targetThreadId = started.threadId;
                    }
                    activeCodexThreadId = targetThreadId;
                    const turnResult = await worker.runTurn({
                        threadId: targetThreadId,
                        text: body.text || 'Say READY.',
                        effort: 'low',
                    });
                    recordLog({
                        timestamp: new Date().toISOString(),
                        requestId,
                        method,
                        path: pathname,
                        status: 200,
                        backend: 'codex-app-server',
                        model: 'codex',
                        ttftMs: turnResult.ttftMs ?? undefined,
                        totalMs: turnResult.totalMs,
                    });
                    sendJson(res, 200, turnResult);
                }
                catch (err) {
                    const status = err instanceof WorkspacePathDeniedError ? err.statusCode : 500;
                    sendJson(res, status, {
                        error: { message: err instanceof Error ? err.message : 'Codex runTurn failed' },
                    });
                }
                return;
            }
            if (method === 'POST' && pathname === '/api/admin/codex/turn/interrupt') {
                try {
                    const { text: raw } = await readRequestBody(req);
                    const body = JSON.parse(raw || '{}');
                    const targetThreadId = body.threadId?.trim() || activeCodexThreadId;
                    if (!codexWorker || !targetThreadId) {
                        sendJson(res, 400, { error: { message: 'No active Codex thread to interrupt.' } });
                        return;
                    }
                    const turnId = body.turnId?.trim() || codexWorker.getActiveTurnId(targetThreadId);
                    if (!turnId) {
                        sendJson(res, 400, { error: { message: 'No active turn in flight on this thread.' } });
                        return;
                    }
                    const out = await codexWorker.interruptTurn({ threadId: targetThreadId, turnId });
                    sendJson(res, 200, out);
                }
                catch (err) {
                    sendJson(res, 500, {
                        error: { message: err instanceof Error ? err.message : 'Codex interruptTurn failed' },
                    });
                }
                return;
            }
            sendJson(res, 404, { error: { message: `Unknown admin route ${method} ${pathname}` } });
            return;
        }
        // 1. GET /healthz
        if ((method === 'GET' || method === 'HEAD') && pathname === '/healthz') {
            ensureFreshConfig();
            const primary = currentConfig.backends[currentConfig.primaryBackend];
            const localProbe = currentConfig.backends.local.configured && method === 'GET'
                ? await probeLocalBackendReachability(currentConfig.backends.local.baseUrl, {
                    apiKey: currentConfig.backends.local.apiKey,
                })
                : null;
            const primaryReachable = primary.kind === 'local'
                ? primary.configured && Boolean(localProbe?.reachable)
                : primary.configured;
            const secretStoreMode = currentConfig.secretStoreMode ?? 'file-0600-fallback';
            const totalMs = Math.round(performance.now() - requestStartMs);
            sendJson(res, 200, {
                status: 'ok',
                version: PKG_VERSION,
                bind: `${currentConfig.host}:${actualPort}`,
                primaryBackend: {
                    kind: primary.kind,
                    configured: primary.kind === 'local' ? primaryReachable : primary.configured,
                    configuredRaw: primary.configured,
                    reachable: primaryReachable,
                    defaultModel: primary.defaultModel,
                },
                failoverBackend: currentConfig.allowCrossProviderFailover
                    ? currentConfig.failoverBackend
                    : null,
                allowCrossProviderFailover: currentConfig.allowCrossProviderFailover,
                capabilities: {
                    vision: true,
                    streaming: true,
                    jsonObject: true,
                    backends: CHAT_BACKEND_KINDS,
                },
                nidoAccount: buildHealthNidoSummary(),
                secretStore: {
                    mode: secretStoreMode,
                    label: formatSecretStoreLabel(secretStoreMode),
                },
                supportedEndpoints: [
                    'GET /healthz',
                    'GET /v1/models',
                    'GET /v1/account',
                    'POST /v1/account/sync',
                    'POST /v1/chat/completions',
                ],
                supportedChatFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
            });
            recordLog({
                timestamp: new Date().toISOString(),
                requestId,
                method,
                path: pathname,
                status: 200,
                totalMs,
            });
            return;
        }
        // 1a. GET /v1/account & POST /v1/account/sync (Loopback SSO for NidoOffice / NidoAgent / NidoBox)
        if ((method === 'GET' && pathname === '/v1/account') ||
            (method === 'POST' && pathname === '/v1/account/sync')) {
            if (isRateLimited(authFailTimestamps, AUTH_FAIL_LIMIT_PER_MIN)) {
                sendJson(res, 429, {
                    error: {
                        type: 'rate_limit_error',
                        code: 'rate_limited',
                        message: 'Too many authentication failures. Try again later.',
                    },
                }, { 'Retry-After': '60' });
                return;
            }
            if (!timingSafeBearerMatch(req.headers.authorization, currentConfig.clientToken)) {
                recordRateEvent(authFailTimestamps);
                const totalMs = Math.round(performance.now() - requestStartMs);
                sendJson(res, 401, {
                    error: {
                        type: 'authentication_error',
                        code: 'invalid_client_token',
                        message: 'Missing or invalid NidoAI client bearer token.',
                    },
                });
                recordLog({
                    timestamp: new Date().toISOString(),
                    requestId,
                    method,
                    path: pathname,
                    status: 401,
                    totalMs,
                });
                return;
            }
            ensureFreshConfig();
            const wantSync = pathname === '/v1/account/sync' || url.searchParams.get('refresh') === '1';
            let statusCode = 200;
            let bodyPayload;
            if (wantSync) {
                const syncRes = await syncNidoAccountNow();
                statusCode = syncRes.ok ? 200 : syncRes.status;
                bodyPayload = {
                    ok: syncRes.ok,
                    ...(syncRes.error ? { error: syncRes.error } : {}),
                    ...syncRes.payload,
                };
            }
            else {
                bodyPayload = {
                    ok: true,
                    ...buildSanitizedAccountPayload(),
                };
            }
            const totalMs = Math.round(performance.now() - requestStartMs);
            sendJson(res, statusCode, bodyPayload);
            recordLog({
                timestamp: new Date().toISOString(),
                requestId,
                method,
                path: pathname,
                status: statusCode,
                totalMs,
            });
            return;
        }
        // 1b. GET /v1/models (Standard OpenAI-compatible model discovery for unmodified apps)
        if ((method === 'GET' || method === 'HEAD') && pathname === '/v1/models') {
            if (isRateLimited(authFailTimestamps, AUTH_FAIL_LIMIT_PER_MIN)) {
                sendJson(res, 429, {
                    error: {
                        type: 'rate_limit_error',
                        code: 'rate_limited',
                        message: 'Too many authentication failures. Try again later.',
                    },
                }, { 'Retry-After': '60' });
                return;
            }
            if (!timingSafeBearerMatch(req.headers.authorization, currentConfig.clientToken)) {
                recordRateEvent(authFailTimestamps);
                const totalMs = Math.round(performance.now() - requestStartMs);
                sendJson(res, 401, {
                    error: {
                        type: 'authentication_error',
                        code: 'invalid_client_token',
                        message: 'Missing or invalid NidoAI client bearer token.',
                    },
                });
                recordLog({
                    timestamp: new Date().toISOString(),
                    requestId,
                    method,
                    path: pathname,
                    status: 401,
                    totalMs,
                });
                return;
            }
            ensureFreshConfig();
            const nowSec = Math.floor(Date.now() / 1000);
            const primary = currentConfig.backends[currentConfig.primaryBackend];
            const localProbe = method === 'GET' && currentConfig.backends.local.configured
                ? await probeLocalBackendReachability(currentConfig.backends.local.baseUrl, {
                    apiKey: currentConfig.backends.local.apiKey,
                })
                : null;
            const localReachable = Boolean(localProbe?.reachable);
            const isBackendReady = (kind) => {
                const b = currentConfig.backends[kind];
                return kind === 'local' ? b.configured && localReachable : b.configured;
            };
            const modelsList = [
                {
                    id: 'auto',
                    object: 'model',
                    created: nowSec,
                    owned_by: 'nidoai',
                    configured: isBackendReady(currentConfig.primaryBackend),
                    reachable: isBackendReady(currentConfig.primaryBackend),
                },
                {
                    id: `${currentConfig.primaryBackend}/${primary.defaultModel}`,
                    object: 'model',
                    created: nowSec,
                    owned_by: currentConfig.primaryBackend,
                    configured: isBackendReady(currentConfig.primaryBackend),
                    reachable: isBackendReady(currentConfig.primaryBackend),
                },
                {
                    id: primary.defaultModel,
                    object: 'model',
                    created: nowSec,
                    owned_by: currentConfig.primaryBackend,
                    configured: isBackendReady(currentConfig.primaryBackend),
                    reachable: isBackendReady(currentConfig.primaryBackend),
                },
            ];
            const seenIds = new Set(modelsList.map((m) => m.id));
            for (const kind of CHAT_BACKEND_KINDS) {
                const b = currentConfig.backends[kind];
                const prefixedId = `${kind}/${b.defaultModel}`;
                if (!seenIds.has(prefixedId)) {
                    seenIds.add(prefixedId);
                    modelsList.push({
                        id: prefixedId,
                        object: 'model',
                        created: nowSec,
                        owned_by: kind,
                        configured: isBackendReady(kind),
                        reachable: isBackendReady(kind),
                    });
                }
            }
            if (method === 'GET' && isBackendReady(currentConfig.primaryBackend)) {
                if (primary.kind === 'nido') {
                    for (const id of NIDO_CURATED_MODELS) {
                        if (!seenIds.has(id)) {
                            seenIds.add(id);
                            modelsList.push({
                                id,
                                object: 'model',
                                created: nowSec,
                                owned_by: 'nido',
                                configured: true,
                                reachable: true,
                            });
                        }
                    }
                }
                else {
                    try {
                        const modelHeaders = primary.kind === 'anthropic'
                            ? {
                                'x-api-key': primary.apiKey,
                                'anthropic-version': ANTHROPIC_API_VERSION,
                            }
                            : { Authorization: `Bearer ${primary.apiKey}` };
                        const upRes = await fetch(`${primary.baseUrl}/models`, {
                            method: 'GET',
                            headers: modelHeaders,
                            signal: AbortSignal.timeout(3000),
                        });
                        if (upRes.ok) {
                            const upJson = (await upRes.json());
                            if (Array.isArray(upJson.data)) {
                                for (const item of upJson.data) {
                                    if (typeof item?.id === 'string' && item.id.trim() && !seenIds.has(item.id.trim())) {
                                        const id = item.id.trim();
                                        seenIds.add(id);
                                        modelsList.push({
                                            id,
                                            object: 'model',
                                            created: typeof item.created === 'number' ? item.created : nowSec,
                                            owned_by: typeof item.owned_by === 'string'
                                                ? item.owned_by
                                                : currentConfig.primaryBackend,
                                            configured: true,
                                            reachable: true,
                                        });
                                    }
                                }
                            }
                        }
                    }
                    catch {
                        // Fallback gracefully to static model list if upstream /models fails or times out
                    }
                }
            }
            const totalMs = Math.round(performance.now() - requestStartMs);
            sendJson(res, 200, { object: 'list', data: modelsList });
            recordLog({
                timestamp: new Date().toISOString(),
                requestId,
                method,
                path: pathname,
                status: 200,
                totalMs,
            });
            return;
        }
        // 2. POST /v1/chat/completions
        if (method === 'POST' && pathname === '/v1/chat/completions') {
            if (isRateLimited(authFailTimestamps, AUTH_FAIL_LIMIT_PER_MIN)) {
                sendJson(res, 429, {
                    error: {
                        type: 'rate_limit_error',
                        code: 'rate_limited',
                        message: 'Too many authentication failures. Try again later.',
                    },
                }, { 'Retry-After': '60' });
                return;
            }
            if (!timingSafeBearerMatch(req.headers.authorization, currentConfig.clientToken)) {
                recordRateEvent(authFailTimestamps);
                const totalMs = Math.round(performance.now() - requestStartMs);
                sendJson(res, 401, {
                    error: {
                        type: 'authentication_error',
                        code: 'invalid_client_token',
                        message: 'Missing or invalid NidoAI client bearer token.',
                    },
                });
                recordLog({
                    timestamp: new Date().toISOString(),
                    requestId,
                    method,
                    path: pathname,
                    status: 401,
                    totalMs,
                });
                return;
            }
            let parsedBody;
            let rawByteLength = 0;
            try {
                const readRes = await readRequestBody(req, MAX_MULTIMODAL_BODY_BYTES);
                rawByteLength = readRes.byteLength;
                parsedBody = JSON.parse(readRes.text);
            }
            catch (err) {
                const totalMs = Math.round(performance.now() - requestStartMs);
                const isTooLarge = err instanceof PayloadTooLargeError;
                const errStatus = isTooLarge ? 413 : 400;
                sendJson(res, errStatus, {
                    error: {
                        type: 'invalid_request_error',
                        code: isTooLarge ? 'payload_too_large' : 'invalid_json',
                        message: isTooLarge
                            ? `Request body exceeds maximum ${MAX_MULTIMODAL_BODY_BYTES} bytes limit.`
                            : 'Request body must be valid JSON.',
                        supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                    },
                });
                recordLog({
                    timestamp: new Date().toISOString(),
                    requestId,
                    method,
                    path: pathname,
                    status: errStatus,
                    totalMs,
                });
                return;
            }
            const validation = validateChatCompletionRequest(parsedBody);
            if (!validation.ok) {
                const totalMs = Math.round(performance.now() - requestStartMs);
                sendJson(res, validation.status, { error: validation.error });
                recordLog({
                    timestamp: new Date().toISOString(),
                    requestId,
                    method,
                    path: pathname,
                    status: validation.status,
                    totalMs,
                });
                return;
            }
            const reqData = validation.data;
            if (rawByteLength > MAX_TEXT_BODY_BYTES && !hasMultimodalContent(reqData.messages)) {
                const totalMs = Math.round(performance.now() - requestStartMs);
                sendJson(res, 413, {
                    error: {
                        type: 'invalid_request_error',
                        code: 'payload_too_large',
                        message: `Text-only request body exceeds ${MAX_TEXT_BODY_BYTES} bytes limit (up to ${MAX_MULTIMODAL_BODY_BYTES} bytes allowed for multimodal image_url payloads).`,
                        supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                    },
                });
                recordLog({
                    timestamp: new Date().toISOString(),
                    requestId,
                    method,
                    path: pathname,
                    status: 413,
                    totalMs,
                });
                return;
            }
            ensureFreshConfig();
            const wantStream = reqData.stream === true;
            const { candidates, allowFailover } = selectBackendsForRequest(reqData, currentConfig);
            let lastResult = null;
            let failoverUsed = false;
            for (let i = 0; i < candidates.length; i++) {
                const candidate = candidates[i];
                if (i > 0)
                    failoverUsed = true;
                const result = await executeUpstreamCall(candidate, reqData, wantStream, res, requestId, requestStartMs, {
                    stateDir,
                    configDir,
                    useSecretStore,
                    secretStoreOptions,
                    onConfigReloadNeeded: reloadCurrentConfig,
                });
                lastResult = result;
                if (result.ok) {
                    recordLog({
                        timestamp: new Date().toISOString(),
                        requestId,
                        method,
                        path: pathname,
                        status: 200,
                        backend: candidate.kind,
                        model: result.model,
                        stream: wantStream,
                        ttftMs: result.ttftMs,
                        totalMs: result.totalMs,
                        failoverUsed,
                    });
                    return;
                }
                // Never fail over if stream bytes were already emitted to the client
                if (result.emittedStreamBytes) {
                    recordLog({
                        timestamp: new Date().toISOString(),
                        requestId,
                        method,
                        path: pathname,
                        status: 502,
                        backend: candidate.kind,
                        model: result.model,
                        stream: wantStream,
                        ttftMs: result.ttftMs,
                        totalMs: result.totalMs,
                        failoverUsed,
                    });
                    return;
                }
                // Stop if failover is not permitted for this request or if the error is non-retriable (e.g. 400, 401, 403, 404)
                if (!allowFailover || !result.retriableForFailover) {
                    break;
                }
            }
            const finalStatus = lastResult?.status ?? 502;
            const totalMs = Math.round(performance.now() - requestStartMs);
            sendJson(res, finalStatus, lastResult?.errorPayload ?? {
                error: {
                    type: 'upstream_error',
                    message: 'Upstream request failed before streaming content.',
                },
            });
            recordLog({
                timestamp: new Date().toISOString(),
                requestId,
                method,
                path: pathname,
                status: finalStatus,
                backend: candidates[failoverUsed ? candidates.length - 1 : 0]?.kind,
                model: lastResult?.model,
                stream: wantStream,
                totalMs,
                failoverUsed,
            });
            return;
        }
        const totalMs = Math.round(performance.now() - requestStartMs);
        sendJson(res, 404, {
            error: {
                type: 'not_found',
                message: `Unsupported endpoint '${method} ${pathname}'. Supported: GET /healthz, GET /v1/models, GET /v1/account, POST /v1/account/sync, POST /v1/chat/completions`,
            },
        });
        recordLog({
            timestamp: new Date().toISOString(),
            requestId,
            method,
            path: pathname,
            status: 404,
            totalMs,
        });
    };
    const configureHttpTimeouts = (srv) => {
        srv.keepAliveTimeout = 15_000;
        srv.headersTimeout = 20_000;
        srv.requestTimeout = 130_000;
    };
    const closeHttpServerGracefully = (srv, graceMs = 2500) => new Promise((resClose) => {
        let done = false;
        const finish = () => {
            if (done)
                return;
            done = true;
            clearTimeout(forceTimer);
            resClose();
        };
        const forceTimer = setTimeout(() => {
            try {
                srv.closeAllConnections?.();
            }
            catch {
                // ignore
            }
            finish();
        }, graceMs);
        forceTimer.unref?.();
        try {
            srv.closeIdleConnections?.();
            srv.close(() => finish());
        }
        catch {
            finish();
        }
    });
    const server = http.createServer(handleHttpRequest);
    configureHttpTimeouts(server);
    let udsServer = null;
    const rawUdsCandidate = typeof options.udsPath === 'string'
        ? options.udsPath
        : (process.platform === 'linux'
            ? path.join((process.env.XDG_RUNTIME_DIR?.trim() || stateDir), 'nidoai-gateway.sock')
            : '');
    const udsCandidate = rawUdsCandidate && Buffer.byteLength(rawUdsCandidate, 'utf8') <= 96
        ? rawUdsCandidate
        : '';
    const startUdsListenerIfEligible = () => new Promise((resUds) => {
        if (!udsCandidate) {
            resUds(null);
            return;
        }
        try {
            fs.mkdirSync(path.dirname(udsCandidate), { recursive: true, mode: 0o700 });
            if (fs.existsSync(udsCandidate)) {
                fs.unlinkSync(udsCandidate);
            }
            const sockSrv = http.createServer(handleHttpRequest);
            configureHttpTimeouts(sockSrv);
            sockSrv.once('error', () => {
                resUds(null);
            });
            sockSrv.listen(udsCandidate, () => {
                try {
                    fs.chmodSync(udsCandidate, 0o600);
                }
                catch {
                    // ignore chmod failure on special filesystems
                }
                sockSrv.removeAllListeners('error');
                sockSrv.on('error', () => {
                    // ignore transient socket errors
                });
                udsServer = sockSrv;
                activeSocketPath = udsCandidate;
                resUds(udsCandidate);
            });
        }
        catch {
            resUds(null);
        }
    });
    return new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(currentConfig.port, '127.0.0.1', () => {
            const addr = server.address();
            actualPort = typeof addr === 'object' && addr ? addr.port : currentConfig.port;
            // Persistent error handler (replaces the once('error') from promise setup)
            server.removeAllListeners('error');
            server.on('error', (err) => {
                console.error(JSON.stringify({ event: 'server_error', error: err.message, timestamp: new Date().toISOString() }));
            });
            void startUdsListenerIfEligible().then(() => {
                if (options.writeRuntimeDiscovery) {
                    const primary = currentConfig.backends[currentConfig.primaryBackend];
                    writeRuntimeDiscovery({
                        baseUrl: `http://127.0.0.1:${actualPort}`,
                        clientToken: currentConfig.clientToken,
                        ...(activeSocketPath ? { socketPath: activeSocketPath } : {}),
                        capabilities: {
                            vision: primary.kind !== 'nido',
                            streaming: true,
                            jsonObject: true,
                            primaryBackend: primary.kind,
                            primaryConfigured: primary.configured,
                            primaryReachable: primary.kind === 'local' ? false : primary.configured,
                            secretStoreMode: currentConfig.secretStoreMode,
                        },
                    });
                }
                resolve({
                    server,
                    udsServer,
                    socketPath: activeSocketPath,
                    host: '127.0.0.1',
                    port: actualPort,
                    close: async () => {
                        if (codexWorker) {
                            await codexWorker.stop();
                            codexWorker = null;
                        }
                        if (udsServer) {
                            const closingUds = udsServer;
                            udsServer = null;
                            await closeHttpServerGracefully(closingUds);
                        }
                        if (activeSocketPath && fs.existsSync(activeSocketPath)) {
                            try {
                                fs.unlinkSync(activeSocketPath);
                            }
                            catch {
                                // ignore socket cleanup error
                            }
                        }
                        if (options.writeRuntimeDiscovery) {
                            const runtimePath = getRuntimeDiscoveryPath();
                            const currentDisc = readRuntimeDiscovery();
                            if (currentDisc && currentDisc.pid === process.pid && fs.existsSync(runtimePath)) {
                                try {
                                    fs.unlinkSync(runtimePath);
                                }
                                catch {
                                    // ignore runtime cleanup error on shutdown
                                }
                            }
                        }
                        await closeHttpServerGracefully(server);
                    },
                });
            });
        });
    });
}
