export const SUPPORTED_CHAT_COMPLETION_FIELDS = [
    'model',
    'messages',
    'temperature',
    'max_tokens',
    'max_completion_tokens',
    'top_p',
    'stop',
    'stream',
    'response_format',
];
const ALLOWED_FIELD_SET = new Set(SUPPORTED_CHAT_COMPLETION_FIELDS);
const ALLOWED_ROLES = new Set(['system', 'user', 'assistant']);
const ALLOWED_RESPONSE_FORMATS = new Set(['text', 'json_object']);
const ALLOWED_IMAGE_DETAILS = new Set(['auto', 'low', 'high']);
const ALLOWED_DATA_IMAGE_RE = /^data:image\/(?:png|jpeg|jpg|webp|gif);base64,[A-Za-z0-9+/=\s]+$/i;
export function hasMultimodalContent(messages) {
    return messages.some((m) => Array.isArray(m.content) && m.content.some((p) => p.type === 'image_url'));
}
function isAllowedImageUrl(rawUrl) {
    const trimmed = rawUrl.trim();
    if (!trimmed)
        return false;
    if (ALLOWED_DATA_IMAGE_RE.test(trimmed))
        return true;
    try {
        const parsed = new URL(trimmed);
        return parsed.protocol === 'https:';
    }
    catch {
        return false;
    }
}
function validateContentParts(rawParts, msgIndex, role) {
    if (rawParts.length === 0) {
        return {
            ok: false,
            status: 400,
            error: {
                type: 'invalid_request_error',
                code: 'invalid_payload',
                message: `messages[${msgIndex}].content array must not be empty.`,
                supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
            },
        };
    }
    const parts = [];
    for (let j = 0; j < rawParts.length; j++) {
        const rawPart = rawParts[j];
        if (typeof rawPart !== 'object' || rawPart === null || Array.isArray(rawPart)) {
            return {
                ok: false,
                status: 400,
                error: {
                    type: 'invalid_request_error',
                    code: 'invalid_payload',
                    message: `messages[${msgIndex}].content[${j}] must be an object.`,
                    supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                },
            };
        }
        const partObj = rawPart;
        if (partObj.type === 'text') {
            const extraKeys = Object.keys(partObj).filter((k) => k !== 'type' && k !== 'text');
            if (extraKeys.length > 0) {
                return {
                    ok: false,
                    status: 400,
                    error: {
                        type: 'invalid_request_error',
                        code: 'unsupported_field',
                        message: `Unsupported field(s) in messages[${msgIndex}].content[${j}]: ${extraKeys.join(', ')}.`,
                        unsupportedFields: extraKeys.map((k) => `messages.content.${k}`),
                        supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                    },
                };
            }
            if (typeof partObj.text !== 'string' || partObj.text.length === 0) {
                return {
                    ok: false,
                    status: 400,
                    error: {
                        type: 'invalid_request_error',
                        code: 'invalid_payload',
                        message: `messages[${msgIndex}].content[${j}].text must be a non-empty string.`,
                        supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                    },
                };
            }
            parts.push({ type: 'text', text: partObj.text });
            continue;
        }
        if (partObj.type === 'image_url') {
            if (role !== 'user') {
                return {
                    ok: false,
                    status: 400,
                    error: {
                        type: 'invalid_request_error',
                        code: 'invalid_payload',
                        message: `messages[${msgIndex}].content[${j}]: image_url blocks are only permitted on 'user' messages.`,
                        supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                    },
                };
            }
            const extraKeys = Object.keys(partObj).filter((k) => k !== 'type' && k !== 'image_url');
            if (extraKeys.length > 0) {
                return {
                    ok: false,
                    status: 400,
                    error: {
                        type: 'invalid_request_error',
                        code: 'unsupported_field',
                        message: `Unsupported field(s) in messages[${msgIndex}].content[${j}]: ${extraKeys.join(', ')}.`,
                        unsupportedFields: extraKeys.map((k) => `messages.content.${k}`),
                        supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                    },
                };
            }
            const imgObj = partObj.image_url;
            if (typeof imgObj !== 'object' || imgObj === null || Array.isArray(imgObj)) {
                return {
                    ok: false,
                    status: 400,
                    error: {
                        type: 'invalid_request_error',
                        code: 'invalid_payload',
                        message: `messages[${msgIndex}].content[${j}].image_url must be an object with { url }.`,
                        supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                    },
                };
            }
            const imgRec = imgObj;
            const extraImgKeys = Object.keys(imgRec).filter((k) => k !== 'url' && k !== 'detail');
            if (extraImgKeys.length > 0) {
                return {
                    ok: false,
                    status: 400,
                    error: {
                        type: 'invalid_request_error',
                        code: 'unsupported_field',
                        message: `Unsupported field(s) in image_url: ${extraImgKeys.join(', ')}.`,
                        unsupportedFields: extraImgKeys.map((k) => `image_url.${k}`),
                        supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                    },
                };
            }
            if (typeof imgRec.url !== 'string' || !isAllowedImageUrl(imgRec.url)) {
                return {
                    ok: false,
                    status: 400,
                    error: {
                        type: 'invalid_request_error',
                        code: 'invalid_payload',
                        message: `messages[${msgIndex}].content[${j}].image_url.url must be a base64 data:image/* URI or an https:// URL.`,
                        supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                    },
                };
            }
            let detail;
            if (imgRec.detail !== undefined) {
                if (typeof imgRec.detail !== 'string' || !ALLOWED_IMAGE_DETAILS.has(imgRec.detail)) {
                    return {
                        ok: false,
                        status: 400,
                        error: {
                            type: 'invalid_request_error',
                            code: 'invalid_payload',
                            message: `messages[${msgIndex}].content[${j}].image_url.detail must be 'auto', 'low', or 'high'.`,
                            supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                        },
                    };
                }
                detail = imgRec.detail;
            }
            parts.push({
                type: 'image_url',
                image_url: {
                    url: imgRec.url.trim(),
                    ...(detail ? { detail } : {}),
                },
            });
            continue;
        }
        return {
            ok: false,
            status: 400,
            error: {
                type: 'invalid_request_error',
                code: 'unsupported_field',
                message: `Unsupported content part type '${String(partObj.type)}' in messages[${msgIndex}].content[${j}]. Supported: 'text', 'image_url'.`,
                unsupportedFields: [`messages.content.type=${String(partObj.type)}`],
                supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
            },
        };
    }
    return { ok: true, parts };
}
export function validateChatCompletionRequest(body) {
    if (typeof body !== 'object' || body === null || Array.isArray(body)) {
        return {
            ok: false,
            status: 400,
            error: {
                type: 'invalid_request_error',
                code: 'invalid_payload',
                message: 'Request body must be a JSON object.',
                supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
            },
        };
    }
    const record = body;
    const unsupportedFields = Object.keys(record).filter((k) => !ALLOWED_FIELD_SET.has(k));
    if (unsupportedFields.length > 0) {
        return {
            ok: false,
            status: 400,
            error: {
                type: 'invalid_request_error',
                code: 'unsupported_field',
                message: `Unsupported request field(s): ${unsupportedFields.join(', ')}. NidoAI supports text, JSON, and image_url chat completions.`,
                unsupportedFields,
                supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
            },
        };
    }
    if (!Array.isArray(record.messages) || record.messages.length === 0) {
        return {
            ok: false,
            status: 400,
            error: {
                type: 'invalid_request_error',
                code: 'invalid_payload',
                message: "'messages' must be a non-empty array of { role, content } objects.",
                supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
            },
        };
    }
    const validatedMessages = [];
    for (let i = 0; i < record.messages.length; i++) {
        const msg = record.messages[i];
        if (typeof msg !== 'object' || msg === null || Array.isArray(msg)) {
            return {
                ok: false,
                status: 400,
                error: {
                    type: 'invalid_request_error',
                    code: 'invalid_payload',
                    message: `messages[${i}] must be an object.`,
                    supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                },
            };
        }
        const msgObj = msg;
        const extraMsgKeys = Object.keys(msgObj).filter((k) => k !== 'role' && k !== 'content');
        if (extraMsgKeys.length > 0) {
            return {
                ok: false,
                status: 400,
                error: {
                    type: 'invalid_request_error',
                    code: 'unsupported_field',
                    message: `Unsupported field(s) in messages[${i}]: ${extraMsgKeys.join(', ')}. Only 'role' and 'content' are supported.`,
                    unsupportedFields: extraMsgKeys.map((k) => `messages.${k}`),
                    supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                },
            };
        }
        if (typeof msgObj.role !== 'string' || !ALLOWED_ROLES.has(msgObj.role)) {
            return {
                ok: false,
                status: 400,
                error: {
                    type: 'invalid_request_error',
                    code: 'invalid_payload',
                    message: `messages[${i}].role must be one of: system, user, assistant.`,
                    supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                },
            };
        }
        if (typeof msgObj.content === 'string') {
            if (msgObj.content.length === 0) {
                return {
                    ok: false,
                    status: 400,
                    error: {
                        type: 'invalid_request_error',
                        code: 'invalid_payload',
                        message: `messages[${i}].content must be a non-empty string or array of text/image_url parts.`,
                        supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                    },
                };
            }
            validatedMessages.push({
                role: msgObj.role,
                content: msgObj.content,
            });
        }
        else if (Array.isArray(msgObj.content)) {
            const partCheck = validateContentParts(msgObj.content, i, msgObj.role);
            if (!partCheck.ok)
                return partCheck;
            validatedMessages.push({
                role: msgObj.role,
                content: partCheck.parts,
            });
        }
        else {
            return {
                ok: false,
                status: 400,
                error: {
                    type: 'invalid_request_error',
                    code: 'invalid_payload',
                    message: `messages[${i}].content must be a non-empty string or array of text/image_url parts.`,
                    supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                },
            };
        }
    }
    if (record.response_format !== undefined) {
        if (typeof record.response_format !== 'object' ||
            record.response_format === null ||
            Array.isArray(record.response_format)) {
            return {
                ok: false,
                status: 400,
                error: {
                    type: 'invalid_request_error',
                    code: 'invalid_payload',
                    message: "'response_format' must be an object with { type: 'text' | 'json_object' }.",
                    supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                },
            };
        }
        const rf = record.response_format;
        const extraRfKeys = Object.keys(rf).filter((k) => k !== 'type');
        if (extraRfKeys.length > 0 ||
            typeof rf.type !== 'string' ||
            !ALLOWED_RESPONSE_FORMATS.has(rf.type)) {
            return {
                ok: false,
                status: 400,
                error: {
                    type: 'invalid_request_error',
                    code: 'unsupported_field',
                    message: "Only response_format.type in ['text', 'json_object'] is supported.",
                    unsupportedFields: extraRfKeys.length > 0
                        ? extraRfKeys.map((k) => `response_format.${k}`)
                        : [`response_format.type=${String(rf.type)}`],
                    supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                },
            };
        }
    }
    if (record.stream !== undefined && typeof record.stream !== 'boolean') {
        return {
            ok: false,
            status: 400,
            error: {
                type: 'invalid_request_error',
                code: 'invalid_payload',
                message: "'stream' must be a boolean.",
                supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
            },
        };
    }
    if (typeof record.temperature === 'number' && (record.temperature < 0 || record.temperature > 2)) {
        return {
            ok: false,
            status: 400,
            error: {
                type: 'invalid_request_error',
                code: 'invalid_payload',
                message: "'temperature' must be between 0 and 2.",
                supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
            },
        };
    }
    if (typeof record.top_p === 'number' && (record.top_p < 0 || record.top_p > 1)) {
        return {
            ok: false,
            status: 400,
            error: {
                type: 'invalid_request_error',
                code: 'invalid_payload',
                message: "'top_p' must be between 0 and 1.",
                supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
            },
        };
    }
    if (typeof record.max_tokens === 'number' && record.max_tokens < 1) {
        return {
            ok: false,
            status: 400,
            error: {
                type: 'invalid_request_error',
                code: 'invalid_payload',
                message: "'max_tokens' must be >= 1.",
                supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
            },
        };
    }
    if (Array.isArray(record.stop)) {
        for (let i = 0; i < record.stop.length; i++) {
            if (typeof record.stop[i] !== 'string') {
                return {
                    ok: false,
                    status: 400,
                    error: {
                        type: 'invalid_request_error',
                        code: 'invalid_payload',
                        message: `stop[${i}] must be a string.`,
                        supportedFields: SUPPORTED_CHAT_COMPLETION_FIELDS,
                    },
                };
            }
        }
    }
    return {
        ok: true,
        data: {
            ...(typeof record.model === 'string' ? { model: record.model } : {}),
            messages: validatedMessages,
            ...(typeof record.temperature === 'number' ? { temperature: record.temperature } : {}),
            ...(typeof record.max_tokens === 'number' ? { max_tokens: record.max_tokens } : {}),
            ...(typeof record.max_completion_tokens === 'number'
                ? { max_completion_tokens: record.max_completion_tokens }
                : {}),
            ...(typeof record.top_p === 'number' ? { top_p: record.top_p } : {}),
            ...(typeof record.stop === 'string' || Array.isArray(record.stop)
                ? { stop: record.stop }
                : {}),
            ...(typeof record.stream === 'boolean' ? { stream: record.stream } : {}),
            ...(record.response_format !== undefined
                ? {
                    response_format: {
                        type: record.response_format.type,
                    },
                }
                : {}),
        },
    };
}
