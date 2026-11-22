import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
export const MANAGED_SECRET_KEYS = [
    'GEMINI_API_KEY',
    'OPENAI_API_KEY',
    'ANTHROPIC_API_KEY',
    'NIDOAI_CLIENT_TOKEN',
    'NIDO_ACCOUNT_ACCESS_TOKEN',
    'NIDO_ACCOUNT_REFRESH_TOKEN',
];
const KEYRING_SERVICE = 'nidoai';
function defaultRunner(command, args, options) {
    const res = spawnSync(command, args, {
        encoding: 'utf8',
        input: options?.input,
        timeout: options?.timeoutMs ?? 4000,
        stdio: ['pipe', 'pipe', 'pipe'],
    });
    return {
        status: res.status,
        stdout: res.stdout ?? '',
        stderr: res.stderr ?? '',
        error: res.error,
    };
}
function commandAvailable(cmd, platform, runner) {
    const lookup = platform === 'win32' ? 'where' : 'which';
    const res = runner(lookup, [cmd], { timeoutMs: 2000 });
    return !res.error && res.status === 0 && res.stdout.trim().length > 0;
}
function resolveDpapiFilePath(homeDir, env) {
    const stateDir = env.NIDOAI_HOME?.trim() || path.join(homeDir, '.nidoai');
    return path.join(stateDir, 'secrets.dpapi.json');
}
export function detectSecretStoreMode(options = {}) {
    const platform = options.platform ?? process.platform;
    const env = options.env ?? process.env;
    const runner = options.runner ?? defaultRunner;
    const override = (env.NIDOAI_SECRET_STORE ?? '').trim().toLowerCase();
    if (override === 'file' || override === 'env' || override === 'off' || override === '0') {
        return 'file-0600-fallback';
    }
    if (platform === 'darwin') {
        // Avoid hanging on headless SSH macOS sessions unless explicitly forced
        if (env.SSH_CONNECTION && override !== 'keyring') {
            return 'file-0600-fallback';
        }
        return commandAvailable('security', platform, runner)
            ? 'macos-keychain'
            : 'file-0600-fallback';
    }
    if (platform === 'linux') {
        const hasDbus = Boolean(env.DBUS_SESSION_BUS_ADDRESS?.trim());
        if (!hasDbus && override !== 'keyring') {
            return 'file-0600-fallback';
        }
        return commandAvailable('secret-tool', platform, runner)
            ? 'linux-libsecret'
            : 'file-0600-fallback';
    }
    if (platform === 'win32') {
        return commandAvailable('powershell.exe', platform, runner)
            ? 'windows-dpapi'
            : 'file-0600-fallback';
    }
    return 'file-0600-fallback';
}
function escapeSecurityArg(value) {
    return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}
export function readSecretFromStore(key, options = {}) {
    const mode = detectSecretStoreMode(options);
    if (mode === 'file-0600-fallback')
        return null;
    const runner = options.runner ?? defaultRunner;
    const env = options.env ?? process.env;
    const homeDir = options.homeDir ?? os.homedir();
    if (mode === 'linux-libsecret') {
        const res = runner('secret-tool', ['lookup', 'service', KEYRING_SERVICE, 'account', key], {
            timeoutMs: 3000,
        });
        if (!res.error && res.status === 0) {
            const val = res.stdout.replace(/\r?\n$/, '');
            return val.length > 0 ? val : null;
        }
        return null;
    }
    if (mode === 'macos-keychain') {
        const res = runner('security', ['find-generic-password', '-s', KEYRING_SERVICE, '-a', key, '-w'], { timeoutMs: 3000 });
        if (!res.error && res.status === 0) {
            const val = res.stdout.replace(/\r?\n$/, '');
            return val.length > 0 ? val : null;
        }
        return null;
    }
    if (mode === 'windows-dpapi') {
        const filePath = resolveDpapiFilePath(homeDir, env);
        if (!fs.existsSync(filePath))
            return null;
        try {
            const parsed = JSON.parse(fs.readFileSync(filePath, 'utf8'));
            const cipherB64 = typeof parsed[key] === 'string' ? parsed[key] : '';
            if (!cipherB64)
                return null;
            const script = [
                'Add-Type -AssemblyName System.Security',
                '$b = [Convert]::FromBase64String([Console]::In.ReadToEnd().Trim())',
                '$p = [System.Security.Cryptography.ProtectedData]::Unprotect($b, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)',
                '[Console]::Out.Write([System.Text.Encoding]::UTF8.GetString($p))',
            ].join('; ');
            const res = runner('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], { input: cipherB64, timeoutMs: 5000 });
            if (!res.error && res.status === 0 && res.stdout.length > 0) {
                return res.stdout;
            }
        }
        catch {
            return null;
        }
        return null;
    }
    return null;
}
export function writeSecretToStore(key, value, options = {}) {
    const trimmed = value.trim();
    if (!trimmed)
        return false;
    const mode = detectSecretStoreMode(options);
    if (mode === 'file-0600-fallback')
        return false;
    const runner = options.runner ?? defaultRunner;
    const env = options.env ?? process.env;
    const homeDir = options.homeDir ?? os.homedir();
    if (mode === 'linux-libsecret') {
        // Pass secret strictly over stdin, never in argv.
        const res = runner('secret-tool', ['store', `--label=NidoAI ${key}`, 'service', KEYRING_SERVICE, 'account', key], { input: `${trimmed}\n`, timeoutMs: 4000 });
        return !res.error && res.status === 0;
    }
    if (mode === 'macos-keychain') {
        // Use interactive `security -i` over stdin so the secret never appears in process argv.
        const line = `add-generic-password -U -s ${escapeSecurityArg(KEYRING_SERVICE)} -a ${escapeSecurityArg(key)} -w ${escapeSecurityArg(trimmed)}\n`;
        const res = runner('security', ['-i'], { input: line, timeoutMs: 4000 });
        return !res.error && res.status === 0;
    }
    if (mode === 'windows-dpapi') {
        const filePath = resolveDpapiFilePath(homeDir, env);
        const script = [
            'Add-Type -AssemblyName System.Security',
            '$bytes = [System.Text.Encoding]::UTF8.GetBytes([Console]::In.ReadToEnd())',
            '$enc = [System.Security.Cryptography.ProtectedData]::Protect($bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)',
            '[Console]::Out.Write([Convert]::ToBase64String($enc))',
        ].join('; ');
        const res = runner('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], { input: trimmed, timeoutMs: 5000 });
        if (res.error || res.status !== 0 || !res.stdout.trim())
            return false;
        try {
            fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
            let existing = {};
            if (fs.existsSync(filePath)) {
                try {
                    existing = JSON.parse(fs.readFileSync(filePath, 'utf8'));
                }
                catch {
                    existing = {};
                }
            }
            existing[key] = res.stdout.trim();
            fs.writeFileSync(filePath, JSON.stringify(existing, null, 2), {
                encoding: 'utf8',
                mode: 0o600,
            });
            return true;
        }
        catch {
            return false;
        }
    }
    return false;
}
export function deleteSecretFromStore(key, options = {}) {
    const mode = detectSecretStoreMode(options);
    if (mode === 'file-0600-fallback')
        return false;
    const runner = options.runner ?? defaultRunner;
    const env = options.env ?? process.env;
    const homeDir = options.homeDir ?? os.homedir();
    if (mode === 'linux-libsecret') {
        const res = runner('secret-tool', ['clear', 'service', KEYRING_SERVICE, 'account', key], {
            timeoutMs: 3000,
        });
        return !res.error && res.status === 0;
    }
    if (mode === 'macos-keychain') {
        const res = runner('security', ['delete-generic-password', '-s', KEYRING_SERVICE, '-a', key], {
            timeoutMs: 3000,
        });
        return !res.error && res.status === 0;
    }
    if (mode === 'windows-dpapi') {
        const filePath = resolveDpapiFilePath(homeDir, env);
        if (!fs.existsSync(filePath))
            return true;
        try {
            const existing = JSON.parse(fs.readFileSync(filePath, 'utf8'));
            delete existing[key];
            fs.writeFileSync(filePath, JSON.stringify(existing, null, 2), {
                encoding: 'utf8',
                mode: 0o600,
            });
            return true;
        }
        catch {
            return false;
        }
    }
    return false;
}
export function isOsKeyringAvailable(options = {}) {
    return detectSecretStoreMode(options) !== 'file-0600-fallback';
}
export function formatSecretStoreLabel(mode) {
    switch (mode) {
        case 'macos-keychain':
            return 'macOS Keychain';
        case 'linux-libsecret':
            return 'Linux Keyring (libsecret)';
        case 'windows-dpapi':
            return 'Windows DPAPI';
        default:
            return 'Local 0600 file fallback (.env.local)';
    }
}
// Re-export execFileSync guard check only if needed externally
export const _internal = { execFileSync };
