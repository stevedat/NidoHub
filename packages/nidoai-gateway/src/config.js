import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { deleteSecretFromStore, detectSecretStoreMode, readSecretFromStore, writeSecretToStore, } from './security/secret-store.js';
import { resolveNidoAiStateDir } from './runtime-discovery.js';
import { DEFAULT_NIDO_MODEL, resolveNidoAccountProviderConfig, resolveNidoConfigDir, } from './gateway/nido-account-adapter.js';
export const PKG_VERSION = (() => {
    try {
        const pkg = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
        return typeof pkg.version === 'string' ? pkg.version : '0.2.0';
    }
    catch {
        return '0.2.0';
    }
})();
export function isAllowedUpstreamUrl(rawUrl) {
    try {
        const u = new URL(rawUrl);
        if (u.protocol === 'https:')
            return true;
        if (u.protocol === 'http:' &&
            (u.hostname === '127.0.0.1' ||
                u.hostname === 'localhost' ||
                u.hostname === '::1' ||
                u.hostname === '[::1]')) {
            return true;
        }
        return false;
    }
    catch {
        return false;
    }
}
export function resolveDefaultCodexBin() {
    const home = os.homedir();
    const candidates = [
        path.join(home, '.local', 'bin', 'codex'),
        path.join(home, '.local', 'share', 'nido', 'runtimes', 'codex', 'node_modules', '.bin', 'codex'),
        '/opt/homebrew/bin/codex',
        '/usr/local/bin/codex',
        '/usr/bin/codex',
    ];
    const pathEnv = process.env.PATH ?? '';
    for (const dir of pathEnv.split(path.delimiter)) {
        if (dir.trim())
            candidates.push(path.join(dir.trim(), 'codex'));
    }
    for (const c of candidates) {
        if (fs.existsSync(c))
            return c;
    }
    return path.join(home, '.local', 'bin', 'codex');
}
export const CHAT_BACKEND_KINDS = [
    'nido',
    'gemini',
    'openai',
    'anthropic',
    'local',
];
export function isChatBackendKind(value) {
    return (value === 'nido' ||
        value === 'gemini' ||
        value === 'openai' ||
        value === 'anthropic' ||
        value === 'local');
}
export const MIN_CLIENT_TOKEN_LENGTH = 16;
export function maskSecret(secret) {
    if (!secret)
        return '';
    if (secret.length <= 8)
        return '••••••••';
    return `${secret.slice(0, 4)}••••${secret.slice(-3)}`;
}
export function saveConfigUpdatesToEnvLocal(updates, cwd = process.cwd(), options = {}) {
    const envLocalPath = path.join(cwd, '.env.local');
    const { vars: existing, commentLines } = parseEnvFile(envLocalPath, true);
    const useKeyring = options.useSecretStore === true;
    const storeMode = useKeyring
        ? detectSecretStoreMode(options.secretStoreOptions)
        : 'file-0600-fallback';
    const storedInKeyring = [];
    const persistProviderSecret = (envKey, rawVal) => {
        if (rawVal === undefined || rawVal.trim() === '')
            return;
        const trimmed = rawVal.trim();
        if (useKeyring && storeMode !== 'file-0600-fallback') {
            if (writeSecretToStore(envKey, trimmed, options.secretStoreOptions)) {
                delete existing[envKey];
                storedInKeyring.push(envKey);
                return;
            }
        }
        existing[envKey] = trimmed;
    };
    if (updates.primaryBackend && isChatBackendKind(updates.primaryBackend)) {
        existing.NIDOAI_PRIMARY_BACKEND = updates.primaryBackend;
    }
    if (updates.failoverBackend !== undefined) {
        existing.NIDOAI_FAILOVER_BACKEND = updates.failoverBackend ?? '';
    }
    if (updates.allowCrossProviderFailover !== undefined) {
        existing.NIDOAI_ALLOW_CROSS_PROVIDER_FAILOVER = updates.allowCrossProviderFailover ? '1' : '0';
    }
    if (updates.clearNidoAccountTokens === true) {
        deleteSecretFromStore('NIDO_ACCOUNT_ACCESS_TOKEN', options.secretStoreOptions);
        deleteSecretFromStore('NIDO_ACCOUNT_REFRESH_TOKEN', options.secretStoreOptions);
        delete existing.NIDO_ACCOUNT_ACCESS_TOKEN;
        delete existing.NIDO_ACCOUNT_REFRESH_TOKEN;
    }
    else {
        persistProviderSecret('NIDO_ACCOUNT_ACCESS_TOKEN', updates.nidoAccessToken);
        persistProviderSecret('NIDO_ACCOUNT_REFRESH_TOKEN', updates.nidoRefreshToken);
    }
    if (updates.nidoModel !== undefined && updates.nidoModel.trim() !== '') {
        existing.NIDOAI_NIDO_MODEL = updates.nidoModel.trim();
    }
    persistProviderSecret('GEMINI_API_KEY', updates.geminiApiKey);
    if (updates.geminiModel !== undefined && updates.geminiModel.trim() !== '') {
        existing.NIDOAI_GEMINI_MODEL = updates.geminiModel.trim();
    }
    persistProviderSecret('OPENAI_API_KEY', updates.openaiApiKey);
    if (updates.openaiModel !== undefined && updates.openaiModel.trim() !== '') {
        existing.NIDOAI_OPENAI_MODEL = updates.openaiModel.trim();
    }
    persistProviderSecret('ANTHROPIC_API_KEY', updates.anthropicApiKey);
    if (updates.anthropicModel !== undefined && updates.anthropicModel.trim() !== '') {
        existing.NIDOAI_ANTHROPIC_MODEL = updates.anthropicModel.trim();
    }
    if (updates.localBaseUrl !== undefined) {
        existing.NIDOAI_LOCAL_BASE_URL = updates.localBaseUrl.trim();
    }
    if (updates.localModel !== undefined && updates.localModel.trim() !== '') {
        existing.NIDOAI_LOCAL_MODEL = updates.localModel.trim();
    }
    if (updates.clientToken !== undefined && updates.clientToken.trim().length >= MIN_CLIENT_TOKEN_LENGTH) {
        existing.NIDOAI_CLIENT_TOKEN = updates.clientToken.trim();
    }
    fs.mkdirSync(cwd, { recursive: true, mode: 0o700 });
    const varLines = Object.entries(existing).map(([k, v]) => `${k}=${v}`);
    const allLines = [...commentLines, ...varLines];
    fs.writeFileSync(envLocalPath, `${allLines.join('\n')}\n`, { encoding: 'utf8', mode: 0o600 });
    try {
        fs.chmodSync(envLocalPath, 0o600);
    }
    catch {
        // ignore
    }
    return { secretStoreMode: storeMode, storedInKeyring };
}
function parseEnvFile(filePath, preserveComments) {
    if (!fs.existsSync(filePath)) {
        return preserveComments ? { vars: {}, commentLines: [] } : {};
    }
    const vars = {};
    const commentLines = [];
    const content = fs.readFileSync(filePath, 'utf8');
    for (const rawLine of content.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (!line)
            continue;
        if (line.startsWith('#')) {
            commentLines.push(rawLine);
            continue;
        }
        const eqIdx = line.indexOf('=');
        if (eqIdx <= 0)
            continue;
        const key = line.slice(0, eqIdx).trim();
        const val = line.slice(eqIdx + 1).trim().replace(/^["']|["']$/g, '');
        vars[key] = val;
    }
    return preserveComments ? { vars, commentLines } : vars;
}
export function loadConfig(options = {}) {
    const opts = typeof options === 'string' ? { cwd: options } : options;
    const cwd = opts.cwd ?? process.cwd();
    const procEnv = opts.env ?? process.env;
    const readDotEnv = opts.readDotEnvFiles !== false;
    const stateDir = resolveNidoAiStateDir(undefined, procEnv);
    const explicitIsolatedCall = opts.env !== undefined || (opts.cwd !== undefined && path.resolve(opts.cwd) !== path.resolve(stateDir));
    // Read ONLY from NidoAI's own directory (.env and .env.local) and optional state dir when not isolated.
    // Never scrape keys from arbitrary external projects.
    const localEnv = readDotEnv
        ? {
            ...parseEnvFile(path.join(cwd, '.env')),
            ...parseEnvFile(path.join(cwd, '.env.local')),
            ...(!explicitIsolatedCall && path.resolve(stateDir) !== path.resolve(cwd)
                ? parseEnvFile(path.join(stateDir, '.env.local'))
                : {}),
        }
        : {};
    const shouldUseKeyring = opts.useSecretStore === true ||
        (opts.useSecretStore !== false && !explicitIsolatedCall && readDotEnv);
    const storeOpts = {
        env: procEnv,
        ...opts.secretStoreOptions,
    };
    const secretStoreMode = shouldUseKeyring
        ? detectSecretStoreMode(storeOpts)
        : 'file-0600-fallback';
    const get = (key, fallback = '') => {
        const fromProc = procEnv[key];
        if (fromProc !== undefined && fromProc.trim() !== '')
            return fromProc.trim();
        if (localEnv[key] !== undefined && localEnv[key].trim() !== '')
            return localEnv[key].trim();
        return fallback;
    };
    const getSecret = (key) => {
        const fromProc = procEnv[key];
        if (fromProc !== undefined && fromProc.trim() !== '')
            return fromProc.trim();
        if (secretStoreMode !== 'file-0600-fallback') {
            const fromStore = readSecretFromStore(key, storeOpts);
            if (fromStore)
                return fromStore.trim();
        }
        if (localEnv[key] !== undefined && localEnv[key].trim() !== '')
            return localEnv[key].trim();
        return '';
    };
    const nidoAccessToken = getSecret('NIDO_ACCOUNT_ACCESS_TOKEN');
    const nidoRefreshToken = getSecret('NIDO_ACCOUNT_REFRESH_TOKEN');
    const geminiApiKey = getSecret('GEMINI_API_KEY');
    const openaiApiKey = getSecret('OPENAI_API_KEY');
    const anthropicApiKey = getSecret('ANTHROPIC_API_KEY');
    const localBaseUrl = get('NIDOAI_LOCAL_BASE_URL');
    const localApiKey = get('NIDOAI_LOCAL_API_KEY', 'local-no-key');
    const port = Number.parseInt(get('NIDOAI_PORT', '18090'), 10) || 18090;
    // Never fall back to a hardcoded public token. Require explicit NIDOAI_CLIENT_TOKEN.
    const clientToken = getSecret('NIDOAI_CLIENT_TOKEN');
    const clientTokenConfigured = clientToken.length >= MIN_CLIENT_TOKEN_LENGTH;
    const rawPrimary = get('NIDOAI_PRIMARY_BACKEND', 'gemini');
    const primaryBackend = isChatBackendKind(rawPrimary) ? rawPrimary : 'gemini';
    const rawFailover = get('NIDOAI_FAILOVER_BACKEND', '');
    const failoverBackend = isChatBackendKind(rawFailover)
        ? rawFailover
        : null;
    const allowCrossProviderFailover = ['1', 'true', 'yes'].includes(get('NIDOAI_ALLOW_CROSS_PROVIDER_FAILOVER', '0').toLowerCase());
    const configDir = resolveNidoConfigDir(procEnv);
    const nidoProviderCfg = resolveNidoAccountProviderConfig(procEnv, configDir);
    const geminiBaseUrl = get('NIDOAI_GEMINI_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/openai').replace(/\/+$/, '');
    const openaiBaseUrl = get('NIDOAI_OPENAI_BASE_URL', 'https://api.openai.com/v1').replace(/\/+$/, '');
    const anthropicBaseUrl = get('NIDOAI_ANTHROPIC_BASE_URL', 'https://api.anthropic.com/v1').replace(/\/+$/, '');
    const normalizedLocalBaseUrl = (localBaseUrl || 'http://127.0.0.1:11434/v1').replace(/\/+$/, '');
    const backends = {
        nido: {
            kind: 'nido',
            baseUrl: nidoProviderCfg.aiProxyUrl,
            authUrl: nidoProviderCfg.authUrl,
            anonKey: nidoProviderCfg.anonKey,
            apiKey: nidoAccessToken,
            refreshToken: nidoRefreshToken,
            defaultModel: get('NIDOAI_NIDO_MODEL', DEFAULT_NIDO_MODEL),
            configured: Boolean(nidoAccessToken || nidoRefreshToken) && isAllowedUpstreamUrl(nidoProviderCfg.aiProxyUrl),
        },
        gemini: {
            kind: 'gemini',
            baseUrl: geminiBaseUrl,
            apiKey: geminiApiKey,
            defaultModel: get('NIDOAI_GEMINI_MODEL', 'gemini-2.5-flash'),
            configured: Boolean(geminiApiKey) && isAllowedUpstreamUrl(geminiBaseUrl),
        },
        openai: {
            kind: 'openai',
            baseUrl: openaiBaseUrl,
            apiKey: openaiApiKey,
            defaultModel: get('NIDOAI_OPENAI_MODEL', 'gpt-4o-mini'),
            configured: Boolean(openaiApiKey) && isAllowedUpstreamUrl(openaiBaseUrl),
        },
        anthropic: {
            kind: 'anthropic',
            baseUrl: anthropicBaseUrl,
            apiKey: anthropicApiKey,
            defaultModel: get('NIDOAI_ANTHROPIC_MODEL', 'claude-sonnet-4-5'),
            configured: Boolean(anthropicApiKey) && isAllowedUpstreamUrl(anthropicBaseUrl),
        },
        local: {
            kind: 'local',
            baseUrl: normalizedLocalBaseUrl,
            apiKey: localApiKey,
            defaultModel: get('NIDOAI_LOCAL_MODEL', 'llama3.2'),
            configured: Boolean(localBaseUrl) && isAllowedUpstreamUrl(normalizedLocalBaseUrl),
        },
    };
    return {
        host: '127.0.0.1',
        port,
        clientToken,
        clientTokenConfigured,
        primaryBackend,
        failoverBackend,
        allowCrossProviderFailover,
        backends,
        codexBin: get('NIDOAI_CODEX_BIN', resolveDefaultCodexBin()),
        secretStoreMode,
        stateDir: explicitIsolatedCall ? path.resolve(cwd) : stateDir,
        configDir,
    };
}
/**
 * Used by CLI (`nidoai serve` / `nidoai init`) on a fresh machine:
 * if no NIDOAI_CLIENT_TOKEN is configured yet, generates a unique random token
 * and persists it to `.env.local` with 0600 permissions.
 */
export function ensureClientTokenBootstrapped(cwd = process.cwd(), options = {}) {
    const initial = loadConfig({ cwd, useSecretStore: options.useSecretStore });
    if (initial.clientTokenConfigured) {
        return { config: initial, bootstrapped: false };
    }
    const randomToken = `nidoai_${crypto.randomBytes(24).toString('hex')}`;
    saveConfigUpdatesToEnvLocal({ clientToken: randomToken }, cwd);
    return { config: loadConfig({ cwd, useSecretStore: options.useSecretStore }), bootstrapped: true };
}
