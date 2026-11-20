import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { CodexAppServerWorker } from '../agent/codex-worker.js';
import { ANTHROPIC_API_VERSION } from '../gateway/anthropic-adapter.js';
import { probeLocalBackendReachability } from '../gateway/live-probe.js';
import { readAgentsStatus } from '../runtime-discovery.js';
import { detectSecretStoreMode, formatSecretStoreLabel } from '../security/secret-store.js';
const execFileAsync = promisify(execFile);
async function probeChatBackend(name, baseUrl, apiKey, configured) {
    if (!configured) {
        return {
            name: `backend:${name}`,
            track: 'track1-chat-backend',
            status: 'UNCONFIGURED',
            detail: `${name} backend is not configured.`,
        };
    }
    if (name === 'local') {
        const probe = await probeLocalBackendReachability(baseUrl, {
            apiKey,
            timeoutMs: 1500,
            force: true,
        });
        if (probe.reachable) {
            return {
                name: 'backend:local',
                track: 'track1-chat-backend',
                status: 'PASS',
                latencyMs: probe.latencyMs,
                detail: `Reachable at ${baseUrl} (${probe.models.length} model(s) discovered)`,
            };
        }
        return {
            name: 'backend:local',
            track: 'track1-chat-backend',
            status: 'FAIL',
            latencyMs: probe.latencyMs,
            detail: probe.error ?? `Local endpoint ${baseUrl} is not reachable`,
        };
    }
    const t0 = performance.now();
    try {
        const headers = name === 'anthropic'
            ? {
                'x-api-key': apiKey,
                'anthropic-version': ANTHROPIC_API_VERSION,
            }
            : { Authorization: `Bearer ${apiKey}` };
        const res = await fetch(`${baseUrl}/models`, {
            method: 'GET',
            headers,
            signal: AbortSignal.timeout(5000),
        });
        const latencyMs = Math.round(performance.now() - t0);
        if (res.ok) {
            return {
                name: `backend:${name}`,
                track: 'track1-chat-backend',
                status: 'PASS',
                latencyMs,
                detail: `HTTP ${res.status} OK from ${baseUrl}/models`,
            };
        }
        return {
            name: `backend:${name}`,
            track: 'track1-chat-backend',
            status: 'FAIL',
            latencyMs,
            detail: `HTTP ${res.status} from ${baseUrl}/models`,
        };
    }
    catch (err) {
        return {
            name: `backend:${name}`,
            track: 'track1-chat-backend',
            status: 'FAIL',
            latencyMs: Math.round(performance.now() - t0),
            detail: err instanceof Error ? err.message : 'Connection failed',
        };
    }
}
async function probeCodexWorker(codexBin) {
    if (!fs.existsSync(codexBin)) {
        return {
            name: 'agent:codex-app-server',
            track: 'track2-agent-worker',
            status: 'UNCONFIGURED',
            detail: `Codex CLI not installed at ${codexBin}`,
        };
    }
    let version = 'unknown';
    let loginStatus = 'unknown';
    try {
        const vRes = await execFileAsync(codexBin, ['--version'], { timeout: 3000 });
        version = vRes.stdout.trim();
        const lRes = await execFileAsync(codexBin, ['login', 'status'], { timeout: 3000 });
        loginStatus = (lRes.stdout || lRes.stderr).trim();
    }
    catch {
        // ignore
    }
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'nidoai-doctor-'));
    const worker = new CodexAppServerWorker({
        codexBin,
        cwd: tmpDir,
        sandboxMode: 'read-only',
        approvalPolicy: 'never',
    });
    try {
        const { handshakeMs } = await worker.start(8000);
        await worker.stop();
        return {
            name: 'agent:codex-app-server',
            track: 'track2-agent-worker',
            status: 'PASS',
            latencyMs: handshakeMs,
            detail: `${version} | ${loginStatus} | stdio initialize handshake in ${handshakeMs} ms`,
        };
    }
    catch (err) {
        await worker.stop();
        return {
            name: 'agent:codex-app-server',
            track: 'track2-agent-worker',
            status: 'FAIL',
            detail: err instanceof Error ? err.message : 'Handshake failed',
        };
    }
    finally {
        fs.rmSync(tmpDir, { recursive: true, force: true });
    }
}
async function probeOtherLocalClis() {
    const results = [];
    const home = os.homedir();
    // 1. agy
    const agyPath = path.join(home, '.local/bin/agy');
    if (fs.existsSync(agyPath)) {
        try {
            const t0 = performance.now();
            const { stdout } = await execFileAsync(agyPath, ['--version'], { timeout: 3000 });
            results.push({
                name: 'cli:agy',
                track: 'local-cli-probe',
                status: 'PARTIAL',
                latencyMs: Math.round(performance.now() - t0),
                detail: `agy ${stdout.trim()} installed (native CLI only; excluded from third-party HTTP proxy per ToS)`,
            });
        }
        catch (err) {
            results.push({
                name: 'cli:agy',
                track: 'local-cli-probe',
                status: 'FAIL',
                detail: err instanceof Error ? err.message : 'Failed to run agy --version',
            });
        }
    }
    else {
        results.push({
            name: 'cli:agy',
            track: 'local-cli-probe',
            status: 'UNCONFIGURED',
            detail: 'agy not found in ~/.local/bin/agy',
        });
    }
    // 2. kiro-cli
    const kiroPath = path.join(home, '.local/bin/kiro-cli');
    if (fs.existsSync(kiroPath)) {
        try {
            const { stdout } = await execFileAsync(kiroPath, ['--version'], { timeout: 3000 });
            results.push({
                name: 'cli:kiro-cli',
                track: 'local-cli-probe',
                status: 'PARTIAL',
                detail: `${stdout.trim()} installed (ACP integration gate not yet passed; excluded from active worker pool)`,
            });
        }
        catch (err) {
            results.push({
                name: 'cli:kiro-cli',
                track: 'local-cli-probe',
                status: 'FAIL',
                detail: err instanceof Error ? err.message : 'Failed to probe kiro-cli',
            });
        }
    }
    // 3. cursor-agent
    const cursorPath = path.join(home, '.local/bin/cursor-agent');
    if (fs.existsSync(cursorPath)) {
        results.push({
            name: 'cli:cursor-agent',
            track: 'local-cli-probe',
            status: 'PARTIAL',
            detail: 'cursor-agent binary present (non-JSON stdout in local probe; excluded from active worker pool)',
        });
    }
    // 4. claude, gemini, ollama
    for (const binName of ['claude', 'gemini', 'ollama']) {
        const managedCandidate = path.join(home, '.local', 'share', 'nido', 'runtimes', binName, 'node_modules', '.bin', binName);
        let foundPath = fs.existsSync(managedCandidate) ? managedCandidate : '';
        if (!foundPath) {
            try {
                const lookup = process.platform === 'win32' ? 'where' : 'which';
                const { stdout } = await execFileAsync(lookup, [binName], { timeout: 2000 });
                foundPath = stdout.trim().split(/\r?\n/)[0] ?? '';
            }
            catch {
                foundPath = '';
            }
        }
        if (foundPath) {
            results.push({
                name: `cli:${binName}`,
                track: 'local-cli-probe',
                status: 'PARTIAL',
                detail: `Found at ${foundPath}`,
            });
        }
        else {
            results.push({
                name: `cli:${binName}`,
                track: 'local-cli-probe',
                status: 'UNCONFIGURED',
                detail: `${binName} not found in PATH or managed runtimes`,
            });
        }
    }
    const agentsDoc = readAgentsStatus();
    if (agentsDoc) {
        const signedIn = Object.values(agentsDoc.providers)
            .filter((p) => p.signedIn)
            .map((p) => p.id);
        results.push({
            name: 'discovery:agents-status',
            track: 'track2-agent-worker',
            status: signedIn.length > 0 ? 'PASS' : 'PARTIAL',
            detail: signedIn.length > 0
                ? `Signed-in agent providers: ${signedIn.join(', ')}`
                : 'agents-status.json present (no signed-in interactive agents reported yet)',
        });
    }
    return results;
}
export async function runDoctor(config) {
    const checks = [];
    const secretMode = config.secretStoreMode ?? detectSecretStoreMode();
    checks.push({
        name: 'gateway:client-token',
        track: 'track1-chat-backend',
        status: config.clientTokenConfigured ? 'PASS' : 'UNCONFIGURED',
        detail: config.clientTokenConfigured
            ? 'Explicit NIDOAI_CLIENT_TOKEN configured (>= 16 chars).'
            : 'NIDOAI_CLIENT_TOKEN is not configured or shorter than 16 chars.',
    });
    checks.push({
        name: 'security:secret-store',
        track: 'track1-chat-backend',
        status: secretMode === 'file-0600-fallback' ? 'PARTIAL' : 'PASS',
        detail: secretMode === 'file-0600-fallback'
            ? `${formatSecretStoreLabel(secretMode)} (headless or OS keyring unavailable)`
            : `${formatSecretStoreLabel(secretMode)} active`,
    });
    checks.push(await probeChatBackend('gemini', config.backends.gemini.baseUrl, config.backends.gemini.apiKey, config.backends.gemini.configured));
    checks.push(await probeChatBackend('openai', config.backends.openai.baseUrl, config.backends.openai.apiKey, config.backends.openai.configured));
    checks.push(await probeChatBackend('anthropic', config.backends.anthropic.baseUrl, config.backends.anthropic.apiKey, config.backends.anthropic.configured));
    checks.push(await probeChatBackend('local', config.backends.local.baseUrl, config.backends.local.apiKey, config.backends.local.configured));
    checks.push(await probeCodexWorker(config.codexBin));
    checks.push(...(await probeOtherLocalClis()));
    return checks;
}
