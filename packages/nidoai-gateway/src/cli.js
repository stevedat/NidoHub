#!/usr/bin/env node
import fs from 'node:fs';
import { spawn } from 'node:child_process';
import { ensureClientTokenBootstrapped, loadConfig, maskSecret } from './config.js';
import { runDoctor } from './doctor/doctor.js';
import { startGatewayServer } from './gateway/server.js';
import { getRuntimeDiscoveryPath, readRuntimeDiscovery, resolveNidoAiStateDir, } from './runtime-discovery.js';
function resolveConfigCwd() {
    return process.env.NIDOAI_HOME?.trim() ? resolveNidoAiStateDir() : process.cwd();
}
async function main() {
    const cmd = process.argv[2] ?? 'doctor';
    if (cmd === 'doctor') {
        const config = loadConfig({
            cwd: resolveConfigCwd(),
            useSecretStore: process.env.NIDOAI_SECRET_STORE !== 'file',
        });
        const results = await runDoctor(config);
        console.log('=== NidoAI Doctor (Two-Track Verification) ===');
        for (const r of results) {
            const latencyStr = r.latencyMs !== undefined ? ` (${r.latencyMs} ms)` : '';
            console.log(`[${r.status.padEnd(12)}] ${r.name.padEnd(24)}${latencyStr} -> ${r.detail}`);
        }
        return;
    }
    if (cmd === 'serve') {
        const { config, bootstrapped } = ensureClientTokenBootstrapped(resolveConfigCwd(), {
            useSecretStore: process.env.NIDOAI_SECRET_STORE !== 'file',
        });
        const handle = await startGatewayServer(config, { writeRuntimeDiscovery: true });
        const shutdown = () => {
            handle.close().finally(() => process.exit(0));
        };
        process.once('SIGINT', shutdown);
        process.once('SIGTERM', shutdown);
        console.log(JSON.stringify({
            event: 'gateway_started',
            listen: `http://${handle.host}:${handle.port}`,
            controlPanel: `http://${handle.host}:${handle.port}/`,
            tokenBootstrappedOnFirstRun: bootstrapped,
            primaryBackend: config.primaryBackend,
            defaultModel: config.backends[config.primaryBackend].defaultModel,
        }));
        return;
    }
    if (cmd === 'exec') {
        let args = process.argv.slice(3);
        if (args[0] === '--')
            args = args.slice(1);
        if (args.length === 0) {
            console.error('Usage: nidoai exec -- <command> [args...]');
            process.exit(1);
        }
        const { config } = ensureClientTokenBootstrapped();
        const disc = readRuntimeDiscovery();
        const baseUrl = disc?.baseUrl ?? `http://${config.host}:${config.port}`;
        const token = disc?.clientToken || config.clientToken;
        const defaultModel = config.backends[config.primaryBackend].defaultModel;
        const child = spawn(args[0], args.slice(1), {
            stdio: 'inherit',
            env: {
                ...process.env,
                // Standard OpenAI-compatible SDK variables (Python openai, Node openai, LangChain, LiteLLM, etc.)
                OPENAI_BASE_URL: `${baseUrl}/v1`,
                OPENAI_API_BASE: `${baseUrl}/v1`,
                OPENAI_API_KEY: token,
                OPENAI_MODEL: defaultModel,
                // Native NidoAI variables
                NIDOAI_ENABLED: '1',
                NIDOAI_BASE_URL: baseUrl,
                NIDOAI_CLIENT_TOKEN: token,
                NIDOAI_MODEL: 'auto',
            },
        });
        child.on('error', (err) => {
            console.error(`nidoai exec: ${err.message}`);
            process.exit(127);
        });
        child.on('exit', (code) => process.exit(code ?? 0));
        return;
    }
    if (cmd === 'status') {
        const runtimePath = getRuntimeDiscoveryPath();
        const disc = readRuntimeDiscovery();
        if (!disc) {
            console.log(`🔒 NidoAI Pro: NOT ACTIVATED (${runtimePath} not found)`);
            process.exit(1);
        }
        let alive = false;
        if (disc.pid > 0) {
            try {
                process.kill(disc.pid, 0);
                alive = true;
            }
            catch {
                alive = false;
            }
        }
        console.log(JSON.stringify({
            activated: alive && disc.clientToken.length >= 16,
            runtimePath,
            baseUrl: disc.baseUrl,
            maskedToken: maskSecret(disc.clientToken),
            pid: disc.pid,
            processAlive: alive,
            updatedAt: disc.updatedAt,
        }, null, 2));
        return;
    }
    if (cmd === 'deactivate') {
        const runtimePath = getRuntimeDiscoveryPath();
        const disc = readRuntimeDiscovery();
        if (disc && disc.pid > 0) {
            try {
                process.kill(disc.pid, 'SIGTERM');
            }
            catch {
                // ignore if already stopped
            }
        }
        if (fs.existsSync(runtimePath)) {
            fs.unlinkSync(runtimePath);
        }
        console.log(`✓ Deactivated NidoAI Pro (removed ${runtimePath}).`);
        return;
    }
    console.error(`Unknown command: ${cmd}. Usage: nidoai <doctor|serve|status|deactivate|exec>`);
    process.exit(1);
}
main().catch((err) => {
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
});
