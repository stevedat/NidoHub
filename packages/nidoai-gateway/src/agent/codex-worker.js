import { spawn } from 'node:child_process';
import readline from 'node:readline';
import { PKG_VERSION, resolveDefaultCodexBin } from '../config.js';
export class CodexAppServerWorker {
    codexBin;
    cwd;
    sandboxMode;
    approvalPolicy;
    proc = null;
    rl = null;
    nextId = 1;
    pendingRpcs = new Map();
    activeTurnsByThread = new Map();
    stderrBuffer = [];
    spawnError = null;
    handshakeMs = null;
    constructor(options) {
        this.codexBin = options.codexBin ?? resolveDefaultCodexBin();
        this.cwd = options.cwd;
        this.sandboxMode = options.sandboxMode ?? 'read-only';
        this.approvalPolicy = options.approvalPolicy ?? 'never';
    }
    isRunning() {
        return (this.proc !== null &&
            this.spawnError === null &&
            this.proc.exitCode === null &&
            !this.proc.killed);
    }
    getActiveTurnId(threadId) {
        return this.activeTurnsByThread.get(threadId)?.turnId ?? null;
    }
    failAllInFlight(err) {
        for (const [, pending] of this.pendingRpcs) {
            clearTimeout(pending.timer);
            pending.reject(err);
        }
        this.pendingRpcs.clear();
        for (const [, tracker] of this.activeTurnsByThread) {
            tracker.reject(err);
        }
        this.activeTurnsByThread.clear();
    }
    async start(timeoutMs = 15_000) {
        if (this.isRunning()) {
            return { handshakeMs: this.handshakeMs ?? 0 };
        }
        this.spawnError = null;
        this.stderrBuffer = [];
        const args = [
            '-c',
            `approval_policy="${this.approvalPolicy}"`,
            '-c',
            'features.plugins=false',
            '-s',
            this.sandboxMode,
            'app-server',
        ];
        const t0 = performance.now();
        const child = spawn(this.codexBin, args, {
            cwd: this.cwd,
            stdio: ['pipe', 'pipe', 'pipe'],
        });
        this.proc = child;
        child.on('error', (err) => {
            const wrapped = new Error(`Failed to spawn codex app-server (${this.codexBin}): ${err.message}`, { cause: err });
            wrapped.code = err.code;
            this.spawnError = wrapped;
            this.proc = null;
            this.rl?.close();
            this.rl = null;
            this.failAllInFlight(wrapped);
        });
        child.stdin.on('error', () => {
            // Prevent unhandled EPIPE / ERR_STREAM_DESTROYED when child fails to spawn or exits early;
            // child 'error' or 'exit' listener handles rejecting pending RPCs.
        });
        child.stderr.on('data', (chunk) => {
            const text = chunk.toString('utf8').trim();
            if (text) {
                this.stderrBuffer.push(text);
                if (this.stderrBuffer.length > 25)
                    this.stderrBuffer.shift();
            }
        });
        child.on('exit', (code, signal) => {
            const err = this.spawnError ??
                new Error(`codex app-server exited (code=${String(code)}, signal=${String(signal)}): ${this.stderrBuffer.join(' | ')}`);
            this.proc = null;
            this.rl?.close();
            this.rl = null;
            this.failAllInFlight(err);
        });
        this.rl = readline.createInterface({ input: child.stdout });
        this.rl.on('line', (line) => this.handleLine(line));
        await this.request('initialize', {
            clientInfo: { name: 'nidoai_worker', title: 'NidoAI Worker', version: PKG_VERSION },
            capabilities: {
                experimentalApi: true,
                requestAttestation: false,
                mcpServerOpenaiFormElicitation: false,
                extensions: {},
            },
        }, timeoutMs);
        this.notify('initialized');
        this.handshakeMs = Math.round(performance.now() - t0);
        return { handshakeMs: this.handshakeMs };
    }
    handleLine(line) {
        const trimmed = line.trim();
        if (!trimmed)
            return;
        let parsed;
        try {
            parsed = JSON.parse(trimmed);
        }
        catch {
            return;
        }
        // JSON-RPC response
        if (typeof parsed.id === 'number' &&
            (parsed.result !== undefined || parsed.error !== undefined)) {
            const pending = this.pendingRpcs.get(parsed.id);
            if (!pending)
                return;
            this.pendingRpcs.delete(parsed.id);
            clearTimeout(pending.timer);
            if (parsed.error !== undefined) {
                const errObj = parsed.error;
                pending.reject(new Error(`Codex RPC ${pending.method} failed (${String(errObj.code ?? 'unknown')}): ${String(errObj.message ?? JSON.stringify(errObj))}`));
            }
            else {
                pending.resolve(parsed.result);
            }
            return;
        }
        // Server notifications
        const method = typeof parsed.method === 'string' ? parsed.method : '';
        const params = (parsed.params ?? {});
        if (method === 'turn/started') {
            const threadId = typeof params.threadId === 'string' ? params.threadId : '';
            const turn = (params.turn ?? {});
            const turnId = typeof turn.id === 'string' ? turn.id : null;
            const tracker = this.activeTurnsByThread.get(threadId);
            if (tracker && turnId) {
                tracker.turnId = turnId;
                tracker.onTurnStarted?.(turnId);
            }
            return;
        }
        if (method === 'item/agentMessage/delta') {
            const threadId = typeof params.threadId === 'string' ? params.threadId : '';
            const delta = typeof params.delta === 'string' ? params.delta : '';
            const tracker = this.activeTurnsByThread.get(threadId);
            if (tracker && delta.length > 0) {
                if (tracker.ttftMs === null) {
                    tracker.ttftMs = Math.round(performance.now() - tracker.startMs);
                }
                tracker.text += delta;
                tracker.onDelta?.(delta);
            }
            return;
        }
        if (method === 'turn/completed') {
            const threadId = typeof params.threadId === 'string' ? params.threadId : '';
            const turn = (params.turn ?? {});
            const turnId = typeof turn.id === 'string' ? turn.id : '';
            const status = typeof turn.status === 'string' ? turn.status : 'completed';
            const tracker = this.activeTurnsByThread.get(threadId);
            if (tracker) {
                this.activeTurnsByThread.delete(threadId);
                const totalMs = Math.round(performance.now() - tracker.startMs);
                tracker.resolve({
                    threadId,
                    turnId: turnId || tracker.turnId || '',
                    status,
                    text: tracker.text,
                    ttftMs: tracker.ttftMs,
                    totalMs,
                });
            }
        }
    }
    notify(method, params) {
        if (this.spawnError) {
            throw this.spawnError;
        }
        if (!this.proc || !this.isRunning()) {
            throw new Error('CodexAppServerWorker is not running');
        }
        const frame = { jsonrpc: '2.0', method };
        if (params !== undefined)
            frame.params = params;
        try {
            this.proc.stdin.write(`${JSON.stringify(frame)}\n`);
        }
        catch {
            // Handled via error/exit events
        }
    }
    request(method, params, timeoutMs = 30_000) {
        if (this.spawnError) {
            return Promise.reject(this.spawnError);
        }
        if (!this.proc || !this.isRunning()) {
            return Promise.reject(new Error('CodexAppServerWorker is not running'));
        }
        const id = this.nextId++;
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                this.pendingRpcs.delete(id);
                reject(new Error(`Codex RPC '${method}' timed out after ${timeoutMs}ms`));
            }, timeoutMs);
            this.pendingRpcs.set(id, {
                method,
                resolve: (val) => resolve(val),
                reject,
                timer,
            });
            try {
                this.proc.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
            }
            catch (err) {
                clearTimeout(timer);
                this.pendingRpcs.delete(id);
                reject(err instanceof Error ? err : new Error(String(err)));
            }
        });
    }
    async startThread(options) {
        const targetCwd = options?.cwd ?? this.cwd;
        const res = await this.request('thread/start', {
            cwd: targetCwd,
            ...(options?.model ? { model: options.model } : {}),
        });
        const threadId = res?.thread?.id;
        if (!threadId) {
            throw new Error(`thread/start did not return a valid thread.id: ${JSON.stringify(res)}`);
        }
        return { threadId, cwd: targetCwd };
    }
    async resumeThread(options) {
        const targetCwd = options.cwd ?? this.cwd;
        const res = await this.request('thread/resume', {
            threadId: options.threadId,
            cwd: targetCwd,
        });
        const resumedId = res?.thread?.id ?? options.threadId;
        return { threadId: resumedId, cwd: targetCwd };
    }
    /**
     * Starts a turn and returns `{ turnId, completion }` as soon as `turn/start` answers,
     * allowing the caller to either await `completion` or call `interruptTurn` mid-flight.
     */
    async startTurn(options) {
        if (this.activeTurnsByThread.has(options.threadId)) {
            throw new Error(`Thread ${options.threadId} already has an active turn in flight`);
        }
        const startMs = performance.now();
        let completionResolve;
        let completionReject;
        const completion = new Promise((resolve, reject) => {
            completionResolve = resolve;
            completionReject = reject;
        });
        const tracker = {
            threadId: options.threadId,
            turnId: null,
            startMs,
            ttftMs: null,
            text: '',
            onDelta: options.onDelta,
            resolve: completionResolve,
            reject: completionReject,
        };
        this.activeTurnsByThread.set(options.threadId, tracker);
        try {
            const startRes = await this.request('turn/start', {
                threadId: options.threadId,
                input: [{ type: 'text', text: options.text }],
                ...(options.effort ? { effort: options.effort } : {}),
            });
            const turnId = startRes?.turn?.id ?? tracker.turnId ?? '';
            tracker.turnId = turnId;
            return { turnId, completion };
        }
        catch (err) {
            this.activeTurnsByThread.delete(options.threadId);
            throw err;
        }
    }
    async runTurn(options) {
        const { completion } = await this.startTurn(options);
        return completion;
    }
    async interruptTurn(options) {
        await this.request('turn/interrupt', {
            threadId: options.threadId,
            turnId: options.turnId,
        }, options.timeoutMs ?? 10_000);
        return { interrupted: true };
    }
    async stop() {
        if (!this.proc)
            return;
        const child = this.proc;
        this.proc = null;
        this.rl?.close();
        this.rl = null;
        if (child.exitCode !== null || child.killed)
            return;
        await new Promise((resolve) => {
            const killTimer = setTimeout(() => {
                try {
                    child.kill('SIGKILL');
                }
                catch {
                    // ignore
                }
                resolve();
            }, 2000);
            child.once('exit', () => {
                clearTimeout(killTimer);
                resolve();
            });
            try {
                child.stdin.end();
                child.kill('SIGTERM');
            }
            catch {
                clearTimeout(killTimer);
                resolve();
            }
        });
    }
}
