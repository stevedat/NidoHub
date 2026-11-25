import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
const SAFE_PROVIDER_FIELDS = new Set([
    'id',
    'installed',
    'signedIn',
    'authMethod',
    'accountLabel',
    'plan',
    'binaryPath',
    'version',
    'source',
    'updatedAt',
]);
export function resolveNidoAiStateDir(homeDir, env = process.env) {
    if (!homeDir && env.NIDOAI_HOME?.trim()) {
        return env.NIDOAI_HOME.trim();
    }
    return path.join(homeDir ?? os.homedir(), '.nidoai');
}
export function getRuntimeDiscoveryPath(homeDir) {
    return path.join(resolveNidoAiStateDir(homeDir), 'runtime.json');
}
export function getAgentsStatusPath(homeDir) {
    return path.join(resolveNidoAiStateDir(homeDir), 'agents-status.json');
}
export function writeRuntimeDiscovery(info, homeDir) {
    const dir = resolveNidoAiStateDir(homeDir);
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    try {
        fs.chmodSync(dir, 0o700);
    }
    catch {
        // ignore chmod failures on restricted filesystems
    }
    const filePath = path.join(dir, 'runtime.json');
    const payload = {
        baseUrl: info.baseUrl,
        clientToken: info.clientToken,
        pid: process.pid,
        updatedAt: new Date().toISOString(),
        ...(typeof info.socketPath === 'string' && info.socketPath ? { socketPath: info.socketPath } : {}),
        ...(info.capabilities ? { capabilities: info.capabilities } : {}),
    };
    const tmpPath = path.join(dir, `.runtime.${process.pid}.${Date.now()}.tmp`);
    fs.writeFileSync(tmpPath, JSON.stringify(payload, null, 2), {
        encoding: 'utf8',
        mode: 0o600,
    });
    fs.renameSync(tmpPath, filePath);
    try {
        fs.chmodSync(filePath, 0o600);
    }
    catch {
        // ignore
    }
    return filePath;
}
export function readRuntimeDiscovery(homeDir) {
    const filePath = getRuntimeDiscoveryPath(homeDir);
    if (!fs.existsSync(filePath))
        return null;
    try {
        const raw = fs.readFileSync(filePath, 'utf8');
        const parsed = JSON.parse(raw);
        if (typeof parsed.baseUrl === 'string' && typeof parsed.clientToken === 'string') {
            return {
                baseUrl: parsed.baseUrl,
                clientToken: parsed.clientToken,
                pid: typeof parsed.pid === 'number' ? parsed.pid : 0,
                updatedAt: typeof parsed.updatedAt === 'string' ? parsed.updatedAt : '',
                ...(typeof parsed.socketPath === 'string' && parsed.socketPath
                    ? { socketPath: parsed.socketPath }
                    : {}),
                ...(parsed.capabilities && typeof parsed.capabilities === 'object'
                    ? { capabilities: parsed.capabilities }
                    : {}),
            };
        }
    }
    catch {
        // ignore malformed file
    }
    return null;
}
function sanitizeAgentProviderStatus(id, raw) {
    const clean = {};
    for (const [k, v] of Object.entries(raw)) {
        if (SAFE_PROVIDER_FIELDS.has(k) && v !== undefined) {
            clean[k] = v;
        }
    }
    return {
        id,
        installed: Boolean(clean.installed),
        signedIn: Boolean(clean.signedIn),
        ...(typeof clean.authMethod === 'string' ? { authMethod: clean.authMethod } : {}),
        ...(typeof clean.accountLabel === 'string' ? { accountLabel: clean.accountLabel } : {}),
        ...(typeof clean.plan === 'string' ? { plan: clean.plan } : {}),
        ...(typeof clean.binaryPath === 'string' ? { binaryPath: clean.binaryPath } : {}),
        ...(typeof clean.version === 'string' ? { version: clean.version } : {}),
        ...(clean.source === 'orca' || clean.source === 'nidohub' || clean.source === 'nidoai'
            ? { source: clean.source }
            : {}),
        updatedAt: typeof clean.updatedAt === 'string' && clean.updatedAt.trim()
            ? clean.updatedAt
            : new Date().toISOString(),
    };
}
export function readAgentsStatus(homeDir) {
    const filePath = getAgentsStatusPath(homeDir);
    if (!fs.existsSync(filePath))
        return null;
    try {
        const raw = fs.readFileSync(filePath, 'utf8');
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object' || typeof parsed.providers !== 'object' || !parsed.providers) {
            return null;
        }
        const providers = {};
        for (const [id, entry] of Object.entries(parsed.providers)) {
            if (entry && typeof entry === 'object') {
                providers[id] = sanitizeAgentProviderStatus(id, entry);
            }
        }
        return {
            schemaVersion: 1,
            updatedAt: typeof parsed.updatedAt === 'string' ? parsed.updatedAt : '',
            providers,
        };
    }
    catch {
        return null;
    }
}
export function writeAgentsStatus(providersUpdate, options = {}) {
    const dir = resolveNidoAiStateDir(options.homeDir);
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    const filePath = path.join(dir, 'agents-status.json');
    const mergedProviders = options.merge !== false ? { ...(readAgentsStatus(options.homeDir)?.providers ?? {}) } : {};
    for (const [id, entry] of Object.entries(providersUpdate)) {
        if (!id.trim())
            continue;
        mergedProviders[id] = sanitizeAgentProviderStatus(id, entry);
    }
    const doc = {
        schemaVersion: 1,
        updatedAt: new Date().toISOString(),
        providers: mergedProviders,
    };
    const tmpPath = path.join(dir, `.agents-status.${process.pid}.${Date.now()}.tmp`);
    fs.writeFileSync(tmpPath, JSON.stringify(doc, null, 2), {
        encoding: 'utf8',
        mode: 0o600,
    });
    fs.renameSync(tmpPath, filePath);
    try {
        fs.chmodSync(filePath, 0o600);
    }
    catch {
        // ignore
    }
    return filePath;
}
