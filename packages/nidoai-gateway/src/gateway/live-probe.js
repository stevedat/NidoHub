const DEFAULT_CACHE_TTL_MS = 4_000;
const DEFAULT_TIMEOUT_MS = 450;
let cachedProbe = null;
export function clearLocalProbeCache() {
    cachedProbe = null;
}
function buildOllamaTagsUrl(baseUrl) {
    try {
        const u = new URL(baseUrl);
        if (u.pathname.replace(/\/+$/, '').endsWith('/v1')) {
            u.pathname = '/api/tags';
            u.search = '';
            return u.toString();
        }
        return null;
    }
    catch {
        return null;
    }
}
export async function probeLocalBackendReachability(baseUrl, options = {}) {
    const normalized = baseUrl.trim().replace(/\/+$/, '');
    const now = Date.now();
    const ttl = options.cacheTtlMs ?? DEFAULT_CACHE_TTL_MS;
    if (!options.force &&
        cachedProbe &&
        cachedProbe.url === normalized &&
        now - cachedProbe.result.checkedAt < ttl) {
        return cachedProbe.result;
    }
    const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const t0 = performance.now();
    const tryOpenAiModels = async () => {
        try {
            const res = await fetch(`${normalized}/models`, {
                method: 'GET',
                headers: options.apiKey ? { Authorization: `Bearer ${options.apiKey}` } : undefined,
                signal: AbortSignal.timeout(timeoutMs),
            });
            const latencyMs = Math.round(performance.now() - t0);
            if (!res.ok)
                return null;
            const models = [];
            try {
                const body = (await res.json());
                if (Array.isArray(body.data)) {
                    for (const m of body.data) {
                        if (typeof m?.id === 'string' && m.id.trim())
                            models.push(m.id.trim());
                    }
                }
            }
            catch {
                // non-JSON ok response still counts as reachable
            }
            return { reachable: true, latencyMs, models, checkedAt: Date.now() };
        }
        catch {
            return null;
        }
    };
    const openAiRes = await tryOpenAiModels();
    if (openAiRes) {
        cachedProbe = { url: normalized, result: openAiRes };
        return openAiRes;
    }
    const ollamaTagsUrl = buildOllamaTagsUrl(normalized);
    if (ollamaTagsUrl) {
        try {
            const res = await fetch(ollamaTagsUrl, {
                method: 'GET',
                signal: AbortSignal.timeout(timeoutMs),
            });
            const latencyMs = Math.round(performance.now() - t0);
            if (res.ok) {
                const models = [];
                try {
                    const body = (await res.json());
                    if (Array.isArray(body.models)) {
                        for (const m of body.models) {
                            if (typeof m?.name === 'string' && m.name.trim())
                                models.push(m.name.trim());
                        }
                    }
                }
                catch {
                    // ignore
                }
                const okRes = {
                    reachable: true,
                    latencyMs,
                    models,
                    checkedAt: Date.now(),
                };
                cachedProbe = { url: normalized, result: okRes };
                return okRes;
            }
        }
        catch {
            // fall through to unreachable
        }
    }
    const failRes = {
        reachable: false,
        latencyMs: Math.round(performance.now() - t0),
        models: [],
        checkedAt: Date.now(),
        error: `Local endpoint ${normalized} is not responding.`,
    };
    cachedProbe = { url: normalized, result: failRes };
    return failRes;
}
