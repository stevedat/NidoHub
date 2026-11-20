export const ANTHROPIC_API_VERSION = '2023-06-01';
const DEFAULT_ANTHROPIC_MAX_TOKENS = 4096;
const DATA_IMAGE_RE = /^data:(image\/(?:png|jpeg|jpg|webp|gif));base64,([A-Za-z0-9+/=\s]+)$/i;
function convertPartToAnthropicBlock(part) {
    if (part.type === 'text') {
        return { type: 'text', text: part.text };
    }
    const url = part.image_url.url.trim();
    const dataMatch = url.match(DATA_IMAGE_RE);
    if (dataMatch) {
        const rawMime = dataMatch[1].toLowerCase();
        const mediaType = rawMime === 'image/jpg' ? 'image/jpeg' : rawMime;
        const data = dataMatch[2].replace(/\s+/g, '');
        return {
            type: 'image',
            source: {
                type: 'base64',
                media_type: mediaType,
                data,
            },
        };
    }
    return {
        type: 'image',
        source: {
            type: 'url',
            url,
        },
    };
}
function extractMessageText(content) {
    if (typeof content === 'string')
        return content;
    return content
        .filter((p) => p.type === 'text')
        .map((p) => p.text)
        .join('\n');
}
export function buildAnthropicMessagesPayload(reqData, model) {
    const systemParts = [];
    const messages = [];
    for (const msg of reqData.messages) {
        if (msg.role === 'system') {
            const txt = extractMessageText(msg.content).trim();
            if (txt)
                systemParts.push(txt);
            continue;
        }
        if (typeof msg.content === 'string') {
            messages.push({ role: msg.role, content: msg.content });
        }
        else {
            messages.push({
                role: msg.role,
                content: msg.content.map(convertPartToAnthropicBlock),
            });
        }
    }
    if (reqData.response_format?.type === 'json_object') {
        systemParts.push('Return ONLY valid JSON with no markdown fences or extra commentary.');
    }
    const maxTokens = reqData.max_completion_tokens ?? reqData.max_tokens ?? DEFAULT_ANTHROPIC_MAX_TOKENS;
    const payload = {
        model,
        max_tokens: maxTokens,
        messages,
        stream: true,
    };
    if (systemParts.length > 0) {
        payload.system = systemParts.join('\n\n');
    }
    if (reqData.temperature !== undefined) {
        // Anthropic accepts temperature in [0, 1]
        payload.temperature = Math.min(1, Math.max(0, reqData.temperature));
    }
    if (reqData.top_p !== undefined) {
        payload.top_p = reqData.top_p;
    }
    if (reqData.stop !== undefined) {
        payload.stop_sequences = Array.isArray(reqData.stop) ? reqData.stop : [reqData.stop];
    }
    return payload;
}
export function mapAnthropicStopReason(stopReason) {
    switch (stopReason) {
        case 'max_tokens':
            return 'length';
        case 'end_turn':
        case 'stop_sequence':
        case 'pause_turn':
        default:
            return 'stop';
    }
}
function isRetriableAnthropicStatus(status) {
    return (status === 429 ||
        status === 500 ||
        status === 502 ||
        status === 503 ||
        status === 504 ||
        status === 529);
}
export async function executeAnthropicUpstreamCall(params) {
    const { backend, model, reqData, wantClientStream, res, requestId, requestStartMs, securityHeaders, } = params;
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
    const payload = buildAnthropicMessagesPayload(reqData, model);
    if (payload.messages.length === 0) {
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
                    message: 'At least one non-system message is required for Anthropic backend.',
                },
            },
        };
    }
    const callStartMs = performance.now();
    let upstreamRes;
    try {
        upstreamRes = await fetch(`${backend.baseUrl}/messages`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'x-api-key': backend.apiKey,
                'anthropic-version': ANTHROPIC_API_VERSION,
            },
            body: JSON.stringify(payload),
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
                    message: err instanceof Error ? err.message : 'Anthropic connection failed',
                },
            },
        };
    }
    if (!upstreamRes.ok || !upstreamRes.body) {
        let errBody = { error: { message: `Anthropic HTTP ${upstreamRes.status}` } };
        try {
            errBody = await upstreamRes.json();
        }
        catch {
            // ignore parse failure
        }
        return {
            ok: false,
            emittedStreamBytes: false,
            retriableForFailover: isRetriableAnthropicStatus(upstreamRes.status),
            status: upstreamRes.status === 529 ? 503 : upstreamRes.status,
            model,
            ttftMs: 0,
            totalMs: Math.round(performance.now() - requestStartMs),
            errorPayload: errBody,
        };
    }
    const reader = upstreamRes.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let currentEvent = '';
    let ttftMs = null;
    let emittedStreamBytes = false;
    let aggregatedText = '';
    let finishReason = null;
    let sawMessageStop = false;
    let sawMessageStart = false;
    let sentRoleChunk = false;
    let responseId = `chatcmpl-${requestId}`;
    const created = Math.floor(Date.now() / 1000);
    const emitSseChunk = (delta, chunkFinishReason) => {
        if (!wantClientStream)
            return;
        if (!emittedStreamBytes) {
            res.writeHead(200, {
                ...securityHeaders,
                'Content-Type': 'text/event-stream; charset=utf-8',
                'Cache-Control': 'no-cache',
                Connection: 'keep-alive',
                'x-nidoai-request-id': requestId,
                'x-nidoai-backend': backend.kind,
                'x-nidoai-model': model,
            });
            emittedStreamBytes = true;
        }
        const openAiChunk = {
            id: responseId,
            object: 'chat.completion.chunk',
            created,
            model,
            choices: [
                {
                    index: 0,
                    delta,
                    finish_reason: chunkFinishReason,
                },
            ],
        };
        res.write(`data: ${JSON.stringify(openAiChunk)}\n\n`);
    };
    const processLine = (rawLine) => {
        const trimmed = rawLine.trim();
        if (!trimmed) {
            currentEvent = '';
            return;
        }
        if (trimmed.startsWith('event:')) {
            currentEvent = trimmed.slice(6).trim();
            return;
        }
        if (!trimmed.startsWith('data:'))
            return;
        const dataStr = trimmed.slice(5).trim();
        let parsed;
        try {
            parsed = JSON.parse(dataStr);
        }
        catch {
            return;
        }
        const evtType = typeof parsed.type === 'string' ? parsed.type : currentEvent;
        if (evtType === 'message_start') {
            sawMessageStart = true;
            const msg = (parsed.message ?? {});
            if (typeof msg.id === 'string' && msg.id.trim()) {
                responseId = msg.id.trim();
            }
            return;
        }
        if (evtType === 'content_block_delta') {
            const delta = (parsed.delta ?? {});
            if (delta.type === 'text_delta' && typeof delta.text === 'string') {
                if (delta.text.length > 0 && ttftMs === null) {
                    ttftMs = Math.round(performance.now() - callStartMs);
                }
                if (wantClientStream) {
                    if (!sentRoleChunk) {
                        emitSseChunk({ role: 'assistant', content: delta.text }, null);
                        sentRoleChunk = true;
                    }
                    else {
                        emitSseChunk({ content: delta.text }, null);
                    }
                }
                else {
                    aggregatedText += delta.text;
                }
            }
            return;
        }
        if (evtType === 'message_delta') {
            const delta = (parsed.delta ?? {});
            if (typeof delta.stop_reason === 'string' && delta.stop_reason.trim()) {
                finishReason = mapAnthropicStopReason(delta.stop_reason.trim());
            }
            return;
        }
        if (evtType === 'message_stop') {
            sawMessageStop = true;
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
            res.write(`event: error\ndata: ${JSON.stringify({
                error: {
                    type: 'upstream_stream_error',
                    code: 'incomplete_upstream_response',
                    message: streamErr instanceof Error ? streamErr.message : 'Anthropic stream interrupted',
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
                    message: streamErr instanceof Error ? streamErr.message : 'Anthropic stream interrupted',
                },
            },
        };
    }
    const finalTtftMs = ttftMs ?? Math.round(performance.now() - callStartMs);
    const totalMs = Math.round(performance.now() - requestStartMs);
    const isComplete = sawMessageStart && sawMessageStop && finishReason !== null;
    if (!isComplete) {
        const incompleteErrorPayload = {
            error: {
                type: 'upstream_incomplete_stream',
                code: 'incomplete_upstream_response',
                message: `Anthropic stream ended prematurely (sawMessageStart=${String(sawMessageStart)}, sawMessageStop=${String(sawMessageStop)}, finishReason=${String(finishReason)}).`,
            },
        };
        if (emittedStreamBytes) {
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
        emitSseChunk({}, finishReason);
        res.write('data: [DONE]\n\n');
        res.end();
    }
    else {
        const body = JSON.stringify({
            id: responseId,
            object: 'chat.completion',
            created,
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
        });
        res.writeHead(200, {
            ...securityHeaders,
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': Buffer.byteLength(body).toString(),
            'x-nidoai-request-id': requestId,
            'x-nidoai-backend': backend.kind,
            'x-nidoai-model': model,
            'x-nidoai-ttft-ms': String(finalTtftMs),
            'x-nidoai-total-ms': String(totalMs),
        });
        res.end(body);
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
