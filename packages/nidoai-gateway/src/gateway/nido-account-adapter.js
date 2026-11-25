import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { deleteSecretFromStore, writeSecretToStore } from '../security/secret-store.js';

export const DEFAULT_NIDO_AUTH_URL = 'https://ahjpwfyaadpqyldxfjvi.supabase.co';
// Public publishable Supabase anon client key (role=anon, RLS-protected, matches NidoOffice Desktop).
export const DEFAULT_NIDO_ANON_KEY =
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFoanB3ZnlhYWRwcXlsZHhmanZpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI4MTY3NzYsImV4cCI6MjA4ODM5Mjc3Nn0.QciPYGj8ic8Ltk6UStUNTv1RsHrEhcs4vwWlGBCApl4';
export const DEFAULT_NIDO_AI_PROXY_URL =
    'https://educenter-ai-proxy.ttd1232004.workers.dev/functions/v1/ai-proxy';
export const DEFAULT_NIDO_MODEL = 'nido-llama';
export const NIDO_CURATED_MODELS = ['nido-llama', 'gemini-2.5-flash'];

const ACCESS_TOKEN_MIN_TTL_MS = 2 * 60 * 1000;
const AUTH_TIMEOUT_MS = 10_000;
const AI_PROXY_TIMEOUT_MS = 60_000;

const SAFE_PROFILE_KEYS = new Set([
    'schemaVersion',
    'signedIn',
    'userId',
    'email',
    'fullName',
    'planCode',
    'planName',
    'isPro',
    'source',
    'quota',
    'sync',
    'updatedAt',
]);

const TRUSTED_PROVIDER_HOSTS = new Set([
    'ahjpwfyaadpqyldxfjvi.supabase.co',
    'educenter-ai-proxy.ttd1232004.workers.dev',
]);

function isTrustedProviderHostname(hostname) {
    const h = String(hostname || '').toLowerCase();
    if (TRUSTED_PROVIDER_HOSTS.has(h))
        return true;
    return h.endsWith('.nido-holdings.com') || h.endsWith('.nidoos.vn') || h === 'nido-holdings.com' || h === 'nidoos.vn';
}

function isAllowedEndpointUrl(rawUrl, allowCustom = false) {
    try {
        const u = new URL(rawUrl);
        if (u.protocol === 'http:' &&
            (u.hostname === '127.0.0.1' ||
                u.hostname === 'localhost' ||
                u.hostname === '::1' ||
                u.hostname === '[::1]')) {
            return true;
        }
        if (u.protocol === 'https:') {
            if (u.hostname === '127.0.0.1' || u.hostname === 'localhost' || u.hostname === '::1' || u.hostname === '[::1]') {
                return true;
            }
            return allowCustom || isTrustedProviderHostname(u.hostname);
        }
        return false;
    }
    catch {
        return false;
    }
}

export function resolveNidoConfigDir(env = process.env, homeDir = os.homedir()) {
    const xdg = (env.XDG_CONFIG_HOME ?? '').trim();
    const base = xdg || path.join(homeDir, '.config');
    return path.join(base, 'ubuntu-nido');
}

function readVerifiedRootJsonFile(filePath) {
    try {
        if (!fs.existsSync(filePath))
            return {};
        const st = fs.statSync(filePath);
        if (!st.isFile() || st.uid !== 0 || (st.mode & 0o022) !== 0)
            return {};
        const parsed = JSON.parse(fs.readFileSync(filePath, 'utf8'));
        return parsed && typeof parsed === 'object' ? parsed : {};
    }
    catch {
        return {};
    }
}

export function resolveNidoAccountProviderConfig(env = process.env, _configDir = resolveNidoConfigDir(env)) {
    const sysCfg = readVerifiedRootJsonFile('/etc/ubuntu-nido/account-provider.json');
    const allowCustom = ['1', 'true', 'yes'].includes(String(env.NIDO_ALLOW_CUSTOM_PROVIDER ?? '').trim().toLowerCase()) ||
        Boolean(sysCfg.allowCustomProvider);
    const pick = (envKeys, cfgKey, fallback) => {
        for (const k of envKeys) {
            const v = (env[k] ?? '').trim();
            if (v)
                return v;
        }
        if (typeof sysCfg[cfgKey] === 'string' && sysCfg[cfgKey].trim()) {
            return sysCfg[cfgKey].trim();
        }
        return fallback;
    };
    const authUrl = pick(['NIDO_ACCOUNT_AUTH_URL', 'NIDO_COMPANION_SUPABASE_URL'], 'authUrl', DEFAULT_NIDO_AUTH_URL).replace(/\/+$/, '');
    const anonKey = pick(['NIDO_ACCOUNT_ANON_KEY', 'NIDO_COMPANION_SUPABASE_ANON_KEY'], 'anonKey', DEFAULT_NIDO_ANON_KEY);
    const aiProxyUrl = pick(['NIDO_ACCOUNT_AI_PROXY_URL', 'NIDO_COMPANION_AI_PROXY_URL'], 'aiProxyUrl', DEFAULT_NIDO_AI_PROXY_URL).replace(/\/+$/, '');
    return {
        authUrl: isAllowedEndpointUrl(authUrl, allowCustom) ? authUrl : DEFAULT_NIDO_AUTH_URL,
        anonKey,
        aiProxyUrl: isAllowedEndpointUrl(aiProxyUrl, allowCustom) ? aiProxyUrl : DEFAULT_NIDO_AI_PROXY_URL,
    };
}

export function isJwtFresh(token, minRemainingMs = ACCESS_TOKEN_MIN_TTL_MS) {
    if (!token || typeof token !== 'string')
        return false;
    const parts = token.split('.');
    if (parts.length !== 3 || !parts[0] || !parts[1] || !parts[2])
        return false;
    try {
        const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
        if (typeof payload?.exp !== 'number' || !Number.isFinite(payload.exp))
            return false;
        return payload.exp * 1000 - Date.now() > minRemainingMs;
    }
    catch {
        return false;
    }
}

export function formatNidoPlanName(planCode, isPro) {
    const code = String(planCode ?? '').trim().toLowerCase();
    if (isPro) {
        if (code.includes('enterprise'))
            return 'Nido Enterprise';
        if (code.includes('apple') || code.includes('iap'))
            return 'Nido AI Pro';
        return 'Gói Đồng Hành (Pro)';
    }
    return 'Miễn phí (Free)';
}

export function normalizeQuotaSnapshot(rawQuota) {
    if (!rawQuota || typeof rawQuota !== 'object')
        return null;
    const quota = Number(rawQuota.aiCreditsQuota ?? rawQuota.limit ?? 0);
    const hasExplicitRemaining = rawQuota.aiCreditsRemaining !== undefined || rawQuota.remaining !== undefined;
    const rawRemaining = rawQuota.aiCreditsRemaining !== undefined
        ? Number(rawQuota.aiCreditsRemaining)
        : (rawQuota.remaining !== undefined ? Number(rawQuota.remaining) : NaN);
    const used = rawQuota.aiCreditsUsed !== undefined
        ? Number(rawQuota.aiCreditsUsed)
        : (rawQuota.used !== undefined
            ? Number(rawQuota.used)
            : (hasExplicitRemaining && Number.isFinite(quota) && Number.isFinite(rawRemaining)
                ? Math.max(0, quota - rawRemaining)
                : 0));
    const remaining = hasExplicitRemaining
        ? rawRemaining
        : Math.max(0, quota - used);
    return {
        aiCreditsQuota: Number.isFinite(quota) ? Math.max(0, Math.floor(quota)) : 0,
        aiCreditsUsed: Number.isFinite(used) ? Math.max(0, Math.floor(used)) : 0,
        aiCreditsRemaining: Number.isFinite(remaining) ? Math.max(0, Math.floor(remaining)) : 0,
        aiCreditsResetAt: typeof rawQuota.aiCreditsResetAt === 'string' ? rawQuota.aiCreditsResetAt : '',
        quotaMode: typeof rawQuota.quotaMode === 'string' ? rawQuota.quotaMode : 'monthly_allowance',
    };
}

export function sanitizeNidoAccountProfile(raw) {
    if (!raw || typeof raw !== 'object')
        return null;
    const signedIn = Boolean(raw.signedIn && (raw.email || raw.userId));
    if (!signedIn)
        return null;
    const isPro = Boolean(raw.isPro ?? raw.isCompanionPro ?? false);
    const planCode = typeof raw.planCode === 'string' && raw.planCode.trim()
        ? raw.planCode.trim()
        : (isPro ? 'companion' : 'free');
    const planName = typeof raw.planName === 'string' && raw.planName.trim()
        ? raw.planName.trim()
        : formatNidoPlanName(planCode, isPro);
    const quota = normalizeQuotaSnapshot(raw.quota);
    const syncRaw = raw.sync && typeof raw.sync === 'object' ? raw.sync : {};
    const out = {
        schemaVersion: 1,
        signedIn: true,
        userId: typeof raw.userId === 'string' ? raw.userId.trim() : '',
        email: typeof raw.email === 'string' ? raw.email.trim() : '',
        fullName: typeof raw.fullName === 'string' ? raw.fullName.trim() : '',
        planCode,
        planName,
        isPro,
        source: typeof raw.source === 'string' && raw.source.trim() ? raw.source.trim() : 'nido',
        ...(quota ? { quota } : {}),
        sync: {
            enabled: Boolean(syncRaw.enabled),
            lastSyncAt: typeof syncRaw.lastSyncAt === 'string' ? syncRaw.lastSyncAt : '',
            items: Array.isArray(syncRaw.items) && syncRaw.items.length > 0
                ? syncRaw.items.map((x) => String(x))
                : ['vielotus', 'nidobox', 'ai-policy'],
        },
        updatedAt: typeof raw.updatedAt === 'string' && raw.updatedAt.trim()
            ? raw.updatedAt.trim()
            : new Date().toISOString(),
    };
    const filtered = {};
    for (const [k, v] of Object.entries(out)) {
        if (SAFE_PROFILE_KEYS.has(k)) {
            filtered[k] = v;
        }
    }
    return filtered;
}

export function getNidoAccountProfilePath(configDir = resolveNidoConfigDir()) {
    return path.join(configDir, 'account.json');
}

export function readNidoAccountProfile(configDir = resolveNidoConfigDir()) {
    const filePath = getNidoAccountProfilePath(configDir);
    if (!fs.existsSync(filePath))
        return null;
    try {
        const parsed = JSON.parse(fs.readFileSync(filePath, 'utf8'));
        return sanitizeNidoAccountProfile(parsed);
    }
    catch {
        return null;
    }
}

export function writeNidoAccountProfile(profile, configDir = resolveNidoConfigDir()) {
    const clean = sanitizeNidoAccountProfile(profile);
    if (!clean)
        return null;
    fs.mkdirSync(configDir, { recursive: true, mode: 0o700 });
    const filePath = getNidoAccountProfilePath(configDir);
    const tmpPath = `${filePath}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
    fs.writeFileSync(tmpPath, `${JSON.stringify(clean, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
    fs.chmodSync(tmpPath, 0o600);
    fs.renameSync(tmpPath, filePath);
    try {
        fs.chmodSync(filePath, 0o600);
    }
    catch {
        // ignore chmod errors on special filesystems
    }
    return clean;
}

export function clearNidoAccountProfile(configDir = resolveNidoConfigDir()) {
    const filePath = getNidoAccountProfilePath(configDir);
    try {
        if (fs.existsSync(filePath))
            fs.unlinkSync(filePath);
    }
    catch {
        // ignore
    }
}

function updateEnvLocalTokens(stateDir, updates, useSecretStore, secretStoreOptions) {
    const envPath = path.join(stateDir, '.env.local');
    const vars = {};
    const comments = [];
    if (fs.existsSync(envPath)) {
        for (const raw of fs.readFileSync(envPath, 'utf8').split(/\r?\n/)) {
            const line = raw.trim();
            if (!line)
                continue;
            if (line.startsWith('#')) {
                comments.push(raw);
                continue;
            }
            const eq = line.indexOf('=');
            if (eq > 0) {
                vars[line.slice(0, eq).trim()] = line.slice(eq + 1).trim().replace(/^["']|["']$/g, '');
            }
        }
    }
    for (const key of ['NIDO_ACCOUNT_ACCESS_TOKEN', 'NIDO_ACCOUNT_REFRESH_TOKEN']) {
        if (!(key in updates))
            continue;
        const val = updates[key];
        if (val === null) {
            deleteSecretFromStore(key, secretStoreOptions);
            delete vars[key];
            continue;
        }
        const trimmed = String(val ?? '').trim();
        if (!trimmed)
            continue;
        if (useSecretStore && writeSecretToStore(key, trimmed, secretStoreOptions)) {
            delete vars[key];
        }
        else {
            vars[key] = trimmed;
        }
    }
    if (typeof updates.NIDOAI_PRIMARY_BACKEND === 'string' && updates.NIDOAI_PRIMARY_BACKEND.trim()) {
        vars.NIDOAI_PRIMARY_BACKEND = updates.NIDOAI_PRIMARY_BACKEND.trim();
    }
    fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
    const lines = [...comments, ...Object.entries(vars).map(([k, v]) => `${k}=${v}`)];
    const tmpPath = `${envPath}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
    fs.writeFileSync(tmpPath, `${lines.join('\n')}\n`, { encoding: 'utf8', mode: 0o600 });
    fs.chmodSync(tmpPath, 0o600);
    fs.renameSync(tmpPath, envPath);
}

export async function fetchNidoAccountQuota({ accessToken, providerConfig }) {
    if (!accessToken || !providerConfig?.aiProxyUrl) {
        return { ok: false, status: 401, error: 'Missing Nido Account access token' };
    }
    const headers = {
        Authorization: `Bearer ${accessToken}`,
        Accept: 'application/json',
    };
    if (providerConfig.anonKey) {
        headers.apikey = providerConfig.anonKey;
    }
    try {
        const res = await fetch(`${providerConfig.aiProxyUrl}/quota`, {
            method: 'GET',
            headers,
            signal: AbortSignal.timeout(AUTH_TIMEOUT_MS),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            return {
                ok: false,
                status: res.status,
                error: typeof data?.error === 'string' ? data.error : `HTTP ${res.status}`,
            };
        }
        return {
            ok: true,
            status: res.status,
            userId: typeof data?.userId === 'string' ? data.userId : '',
            planCode: typeof data?.planCode === 'string' ? data.planCode : 'free',
            isPro: Boolean(data?.isCompanionPro ?? data?.isPro ?? false),
            source: typeof data?.source === 'string' ? data.source : 'nido',
            quota: normalizeQuotaSnapshot(data?.quotaSnapshot),
        };
    }
    catch (err) {
        return {
            ok: false,
            status: 0,
            error: err instanceof Error ? err.message : 'Network error',
        };
    }
}

export async function refreshNidoAccountTokens({
    refreshToken,
    providerConfig,
    stateDir,
    useSecretStore = true,
    secretStoreOptions = {},
}) {
    if (!refreshToken || !providerConfig?.authUrl) {
        return { ok: false, status: 401, revoked: true, error: 'Missing refresh token' };
    }
    const headers = { 'Content-Type': 'application/json' };
    if (providerConfig.anonKey) {
        headers.apikey = providerConfig.anonKey;
    }
    try {
        const res = await fetch(`${providerConfig.authUrl}/auth/v1/token?grant_type=refresh_token`, {
            method: 'POST',
            headers,
            body: JSON.stringify({ refresh_token: refreshToken }),
            signal: AbortSignal.timeout(AUTH_TIMEOUT_MS),
        });
        const data = await res.json().catch(() => ({}));
        if (res.status === 400 || res.status === 401 || res.status === 403) {
            return {
                ok: false,
                status: res.status,
                revoked: true,
                error: data?.error_description || data?.msg || data?.error || 'Session revoked',
            };
        }
        if (!res.ok || typeof data?.access_token !== 'string' || !data.access_token) {
            return {
                ok: false,
                status: res.status,
                revoked: false,
                error: data?.error_description || data?.msg || `HTTP ${res.status}`,
            };
        }
        const nextAccess = data.access_token.trim();
        const nextRefresh = typeof data.refresh_token === 'string' && data.refresh_token.trim()
            ? data.refresh_token.trim()
            : refreshToken;
        if (stateDir) {
            updateEnvLocalTokens(stateDir, {
                NIDO_ACCOUNT_ACCESS_TOKEN: nextAccess,
                NIDO_ACCOUNT_REFRESH_TOKEN: nextRefresh,
            }, useSecretStore, secretStoreOptions);
        }
        return {
            ok: true,
            status: 200,
            accessToken: nextAccess,
            refreshToken: nextRefresh,
        };
    }
    catch (err) {
        return {
            ok: false,
            status: 0,
            revoked: false,
            error: err instanceof Error ? err.message : 'Network error',
        };
    }
}

export async function loginNidoAccount({
    email,
    password,
    providerConfig,
    stateDir,
    configDir,
    useSecretStore = true,
    secretStoreOptions = {},
    setAsPrimary = true,
}) {
    const cleanEmail = String(email ?? '').trim();
    const rawPassword = String(password ?? '');
    if (!cleanEmail || !rawPassword) {
        return { ok: false, status: 400, error: 'Vui lòng nhập đầy đủ Email và Mật khẩu.' };
    }
    const headers = { 'Content-Type': 'application/json' };
    if (providerConfig.anonKey) {
        headers.apikey = providerConfig.anonKey;
    }
    let res;
    let data = {};
    try {
        res = await fetch(`${providerConfig.authUrl}/auth/v1/token?grant_type=password`, {
            method: 'POST',
            headers,
            body: JSON.stringify({ email: cleanEmail, password: rawPassword }),
            signal: AbortSignal.timeout(AUTH_TIMEOUT_MS),
        });
        data = await res.json().catch(() => ({}));
    }
    catch (err) {
        return {
            ok: false,
            status: 502,
            error: `Không kết nối được máy chủ Tài khoản Nido: ${err instanceof Error ? err.message : String(err)}`,
        };
    }
    if (!res.ok || typeof data?.access_token !== 'string' || !data.access_token) {
        return {
            ok: false,
            status: res.status || 401,
            error: data?.error_description || data?.msg || data?.error || 'Đăng nhập không thành công. Kiểm tra lại Email hoặc Mật khẩu.',
        };
    }
    const accessToken = data.access_token.trim();
    const refreshToken = typeof data.refresh_token === 'string' ? data.refresh_token.trim() : '';
    const userObj = data.user && typeof data.user === 'object' ? data.user : {};
    const quotaRes = await fetchNidoAccountQuota({ accessToken, providerConfig });
    const prev = readNidoAccountProfile(configDir);
    const isPro = quotaRes.ok ? quotaRes.isPro : false;
    const planCode = quotaRes.ok ? quotaRes.planCode : 'free';
    const profile = writeNidoAccountProfile({
        schemaVersion: 1,
        signedIn: true,
        userId: (quotaRes.ok && quotaRes.userId) || String(userObj.id ?? ''),
        email: String(userObj.email ?? cleanEmail),
        fullName: String(userObj.user_metadata?.full_name ?? userObj.user_metadata?.display_name ?? ''),
        planCode,
        planName: formatNidoPlanName(planCode, isPro),
        isPro,
        source: (quotaRes.ok && quotaRes.source) || 'nido',
        ...(quotaRes.ok && quotaRes.quota ? { quota: quotaRes.quota } : {}),
        sync: prev?.sync ?? { enabled: false, lastSyncAt: '', items: ['vielotus', 'nidobox', 'ai-policy'] },
        updatedAt: new Date().toISOString(),
    }, configDir);
    if (stateDir) {
        const tokenUpdates = {
            NIDO_ACCOUNT_ACCESS_TOKEN: accessToken,
            ...(refreshToken ? { NIDO_ACCOUNT_REFRESH_TOKEN: refreshToken } : {}),
            ...(setAsPrimary ? { NIDOAI_PRIMARY_BACKEND: 'nido' } : {}),
        };
        updateEnvLocalTokens(stateDir, tokenUpdates, useSecretStore, secretStoreOptions);
    }
    return {
        ok: true,
        status: 200,
        profile,
    };
}

function removeNidoProviderFromAiPolicy(configDir = resolveNidoConfigDir()) {
    const policyPath = path.join(configDir, 'ai-policy.json');
    try {
        if (!fs.existsSync(policyPath))
            return;
        const parsed = JSON.parse(fs.readFileSync(policyPath, 'utf8'));
        if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.signedInProviders))
            return;
        if (!parsed.signedInProviders.includes('nido'))
            return;
        const next = {
            ...parsed,
            signedInProviders: parsed.signedInProviders.filter((p) => p !== 'nido'),
            updatedAt: new Date().toISOString(),
        };
        const tmpPath = `${policyPath}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
        fs.writeFileSync(tmpPath, `${JSON.stringify(next, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
        fs.chmodSync(tmpPath, 0o600);
        fs.renameSync(tmpPath, policyPath);
        try {
            fs.chmodSync(policyPath, 0o600);
        }
        catch {
            // ignore chmod errors on special filesystems
        }
    }
    catch {
        // best-effort policy cleanup
    }
}
export async function logoutNidoAccount({
    accessToken,
    providerConfig,
    stateDir,
    configDir,
    secretStoreOptions = {},
}) {
    if (accessToken && providerConfig?.authUrl) {
        const headers = { Authorization: `Bearer ${accessToken}` };
        if (providerConfig.anonKey) {
            headers.apikey = providerConfig.anonKey;
        }
        try {
            await fetch(`${providerConfig.authUrl}/auth/v1/logout`, {
                method: 'POST',
                headers,
                signal: AbortSignal.timeout(5000),
            });
        }
        catch {
            // Best-effort remote revoke; always clear local credentials
        }
    }
    if (stateDir) {
        updateEnvLocalTokens(stateDir, {
            NIDO_ACCOUNT_ACCESS_TOKEN: null,
            NIDO_ACCOUNT_REFRESH_TOKEN: null,
        }, false, secretStoreOptions);
    }
    clearNidoAccountProfile(configDir);
    removeNidoProviderFromAiPolicy(configDir);
    return { ok: true };
}

function extractMessageText(content) {
    if (typeof content === 'string')
        return content;
    if (!Array.isArray(content))
        return '';
    return content
        .filter((part) => part && part.type === 'text' && typeof part.text === 'string')
        .map((part) => part.text)
        .join('\n');
}

function hasImagePart(messages) {
    for (const msg of messages) {
        if (!Array.isArray(msg.content))
            continue;
        for (const part of msg.content) {
            if (part && part.type === 'image_url')
                return true;
        }
    }
    return false;
}

export function buildNidoPromptFromMessages(messages, responseFormat) {
    const systemParts = [];
    const turns = [];
    for (const msg of messages) {
        const txt = extractMessageText(msg.content).trim();
        if (!txt)
            continue;
        if (msg.role === 'system') {
            systemParts.push(txt);
        }
        else if (msg.role === 'assistant') {
            turns.push(`Assistant:\n${txt}`);
        }
        else {
            turns.push(`User:\n${txt}`);
        }
    }
    const sections = [];
    if (systemParts.length > 0) {
        sections.push(systemParts.join('\n\n'));
    }
    if (turns.length === 1 && turns[0].startsWith('User:\n') && systemParts.length === 0) {
        sections.push(turns[0].slice('User:\n'.length));
    }
    else if (turns.length > 0) {
        sections.push(turns.join('\n\n'));
    }
    if (responseFormat?.type === 'json_object') {
        sections.push('Return ONLY valid JSON without markdown code fences.');
    }
    return sections.join('\n\n').trim();
}

export async function executeNidoAccountUpstreamCall(params) {
    const {
        backend,
        model,
        reqData,
        wantClientStream,
        res,
        requestId,
        requestStartMs,
        securityHeaders,
        stateDir,
        configDir,
        useSecretStore = true,
        secretStoreOptions = {},
        onConfigReloadNeeded,
    } = params;

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
                    message: 'Tài khoản Nido chưa đăng nhập. Mở NidoHub để đăng nhập Tài khoản Nido.',
                },
            },
        };
    }

    if (hasImagePart(reqData.messages)) {
        return {
            ok: false,
            emittedStreamBytes: false,
            retriableForFailover: true,
            status: 422,
            model,
            ttftMs: 0,
            totalMs: Math.round(performance.now() - requestStartMs),
            errorPayload: {
                error: {
                    type: 'unsupported_multimodal_backend',
                    message: 'Nido Account AI hiện hỗ trợ văn bản. Để dùng nhận diện ảnh (OCR), hãy thêm khóa Gemini miễn phí hoặc mô hình Ollama hỗ trợ ảnh trong NidoHub.',
                },
            },
        };
    }

    const prompt = buildNidoPromptFromMessages(reqData.messages, reqData.response_format);
    if (!prompt) {
        return {
            ok: false,
            emittedStreamBytes: false,
            retriableForFailover: false,
            status: 400,
            model,
            ttftMs: 0,
            totalMs: Math.round(performance.now() - requestStartMs),
            errorPayload: {
                error: {
                    type: 'invalid_request_error',
                    message: 'Request messages did not contain any text content.',
                },
            },
        };
    }

    const providerConfig = {
        authUrl: backend.authUrl,
        anonKey: backend.anonKey,
        aiProxyUrl: backend.baseUrl,
    };

    let accessToken = backend.apiKey || '';
    let refreshToken = backend.refreshToken || '';

    if ((!accessToken || !isJwtFresh(accessToken)) && refreshToken) {
        const rot = await refreshNidoAccountTokens({
            refreshToken,
            providerConfig,
            stateDir,
            useSecretStore,
            secretStoreOptions,
        });
        if (rot.ok && rot.accessToken) {
            accessToken = rot.accessToken;
            refreshToken = rot.refreshToken || refreshToken;
            if (typeof onConfigReloadNeeded === 'function')
                onConfigReloadNeeded();
        }
    }

    if (!accessToken) {
        return {
            ok: false,
            emittedStreamBytes: false,
            retriableForFailover: false,
            status: 401,
            model,
            ttftMs: 0,
            totalMs: Math.round(performance.now() - requestStartMs),
            errorPayload: {
                error: {
                    type: 'authentication_error',
                    message: 'Phiên đăng nhập Tài khoản Nido đã hết hạn. Vui lòng đăng nhập lại trong NidoHub.',
                },
            },
        };
    }

    const responseMimeType = reqData.response_format?.type === 'json_object'
        ? 'application/json'
        : 'text/plain';
    const maxTokens = reqData.max_completion_tokens ?? reqData.max_tokens ?? 4096;

    const callOnce = async (token, reqId) => {
        const headers = {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
            'x-request-id': reqId,
        };
        if (providerConfig.anonKey) {
            headers.apikey = providerConfig.anonKey;
        }
        return fetch(providerConfig.aiProxyUrl, {
            method: 'POST',
            headers,
            body: JSON.stringify({
                prompt,
                model: model || DEFAULT_NIDO_MODEL,
                taskType: 'text',
                responseMimeType,
                temperature: reqData.temperature ?? 0.4,
                maxTokens,
                requestId: reqId,
            }),
            signal: AbortSignal.timeout(AI_PROXY_TIMEOUT_MS),
        });
    };

    const callStartMs = performance.now();
    let upstreamRes;
    try {
        upstreamRes = await callOnce(accessToken, requestId);
        if (upstreamRes.status === 401 && refreshToken) {
            const rot = await refreshNidoAccountTokens({
                refreshToken,
                providerConfig,
                stateDir,
                useSecretStore,
                secretStoreOptions,
            });
            if (rot.ok && rot.accessToken) {
                accessToken = rot.accessToken;
                if (typeof onConfigReloadNeeded === 'function')
                    onConfigReloadNeeded();
                upstreamRes = await callOnce(accessToken, crypto.randomUUID());
            }
        }
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
                    message: err instanceof Error ? err.message : 'Failed to reach Nido AI proxy',
                },
            },
        };
    }

    const body = await upstreamRes.json().catch(() => ({}));
    if (body?.quotaSnapshot && configDir) {
        const existingProfile = readNidoAccountProfile(configDir);
        if (existingProfile) {
            writeNidoAccountProfile({
                ...existingProfile,
                quota: normalizeQuotaSnapshot(body.quotaSnapshot),
                updatedAt: new Date().toISOString(),
            }, configDir);
        }
    }

    if (!upstreamRes.ok) {
        const msg = typeof body?.error === 'string'
            ? body.error
            : (typeof body?.error?.message === 'string' ? body.error.message : `Nido AI HTTP ${upstreamRes.status}`);
        const retriable = upstreamRes.status === 429 || upstreamRes.status >= 500;
        return {
            ok: false,
            emittedStreamBytes: false,
            retriableForFailover: retriable,
            status: upstreamRes.status,
            model,
            ttftMs: 0,
            totalMs: Math.round(performance.now() - requestStartMs),
            errorPayload: {
                error: {
                    type: upstreamRes.status === 401 || upstreamRes.status === 403
                        ? 'authentication_error'
                        : upstreamRes.status === 429
                            ? 'quota_exceeded'
                            : 'upstream_error',
                    message: msg,
                    ...(body?.quotaSnapshot ? { quotaSnapshot: normalizeQuotaSnapshot(body.quotaSnapshot) } : {}),
                },
            },
        };
    }

    const rawText = typeof body?.raw === 'string'
        ? body.raw
        : typeof body?.text === 'string'
            ? body.text
            : typeof body?.data === 'string'
                ? body.data
                : (body?.data !== undefined ? JSON.stringify(body.data) : '');
    const resolvedModel = typeof body?.model === 'string' && body.model.trim()
        ? body.model.trim()
        : (model || DEFAULT_NIDO_MODEL);
    const ttftMs = Math.round(performance.now() - callStartMs);
    const totalMs = Math.round(performance.now() - requestStartMs);
    const responseId = `chatcmpl-${requestId}`;
    const created = Math.floor(Date.now() / 1000);

    if (wantClientStream) {
        res.writeHead(200, {
            ...securityHeaders,
            'Content-Type': 'text/event-stream; charset=utf-8',
            'Cache-Control': 'no-cache',
            Connection: 'keep-alive',
            'x-nidoai-request-id': requestId,
            'x-nidoai-backend': 'nido',
            'x-nidoai-model': resolvedModel,
            'x-nidoai-ttft-ms': String(ttftMs),
            'x-nidoai-total-ms': String(totalMs),
        });
        const firstChunk = {
            id: responseId,
            object: 'chat.completion.chunk',
            created,
            model: resolvedModel,
            choices: [
                {
                    index: 0,
                    delta: { role: 'assistant', content: rawText },
                    finish_reason: null,
                },
            ],
        };
        const endChunk = {
            id: responseId,
            object: 'chat.completion.chunk',
            created,
            model: resolvedModel,
            choices: [
                {
                    index: 0,
                    delta: {},
                    finish_reason: 'stop',
                },
            ],
        };
        res.write(`data: ${JSON.stringify(firstChunk)}\n\n`);
        res.write(`data: ${JSON.stringify(endChunk)}\n\n`);
        res.write('data: [DONE]\n\n');
        res.end();
    }
    else {
        const jsonBody = JSON.stringify({
            id: responseId,
            object: 'chat.completion',
            created,
            model: resolvedModel,
            choices: [
                {
                    index: 0,
                    message: {
                        role: 'assistant',
                        content: rawText,
                    },
                    finish_reason: 'stop',
                },
            ],
        });
        res.writeHead(200, {
            ...securityHeaders,
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': Buffer.byteLength(jsonBody).toString(),
            'x-nidoai-request-id': requestId,
            'x-nidoai-backend': 'nido',
            'x-nidoai-model': resolvedModel,
            'x-nidoai-ttft-ms': String(ttftMs),
            'x-nidoai-total-ms': String(totalMs),
        });
        res.end(jsonBody);
    }

    return {
        ok: true,
        emittedStreamBytes: wantClientStream,
        retriableForFailover: false,
        status: 200,
        model: resolvedModel,
        ttftMs,
        totalMs,
    };
}
