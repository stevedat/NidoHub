#!/usr/bin/env node
/**
 * NidoAgent CLI (Ubuntu-Nido)
 * Zero-dependency ESM client talking to the local NidoAI Gateway.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';

const VERSION = '0.1.0';
const MAX_ATTACH_BYTES = 65_536;
const OFFICE_EXTS = new Set(['.docx', '.xlsx', '.pptx']);

function stateDirCandidates() {
  const dirs = [];
  if (process.env.NIDOAI_HOME) {
    dirs.push(process.env.NIDOAI_HOME);
  }
  dirs.push(path.join(os.homedir(), '.nidoai'));
  return [...new Set(dirs)];
}

function parseEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return {};
  const out = {};
  for (const raw of fs.readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq > 0) {
      out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim().replace(/^["']|["']$/g, '');
    }
  }
  return out;
}

function resolveGatewayConnection() {
  let baseUrl = process.env.NIDOAI_BASE_URL || '';
  let clientToken = process.env.NIDOAI_CLIENT_TOKEN || '';
  let runtimeFound = null;

  for (const dir of stateDirCandidates()) {
    const rtPath = path.join(dir, 'runtime.json');
    if (fs.existsSync(rtPath)) {
      try {
        const rt = JSON.parse(fs.readFileSync(rtPath, 'utf8'));
        runtimeFound = rtPath;
        if (!baseUrl && typeof rt.baseUrl === 'string') baseUrl = rt.baseUrl;
        if (!clientToken && typeof rt.clientToken === 'string') clientToken = rt.clientToken;
      } catch {
        // ignore malformed runtime file
      }
    }
    const envLocal = parseEnvFile(path.join(dir, '.env.local'));
    if (!clientToken && envLocal.NIDOAI_CLIENT_TOKEN) {
      clientToken = envLocal.NIDOAI_CLIENT_TOKEN;
    }
    if (!baseUrl && envLocal.NIDOAI_PORT) {
      baseUrl = `http://127.0.0.1:${envLocal.NIDOAI_PORT}`;
    }
  }

  if (!baseUrl) {
    const port = process.env.NIDOAI_GATEWAY_PORT || process.env.NIDOAI_PORT || '18090';
    baseUrl = `http://127.0.0.1:${port}`;
  }

  return {
    baseUrl: baseUrl.replace(/\/+$/, ''),
    clientToken,
    runtimeFound,
  };
}

function printHelp() {
  console.log(`NidoAgent CLI v${VERSION} (Ubuntu-Nido)

Usage:
  nidoagent status | doctor                                 Kiểm tra kết nối NidoAI Gateway
  nidoagent ask [--file <path>] [--out <path>] <prompt>     Gửi câu hỏi / tệp và tùy chọn xuất báo cáo
  nidoagent chat                                            Mở phiên hội thoại tương tác
  nidoagent --version | --help                              Hiện thông tin phiên bản / trợ giúp
`);
}

async function cmdStatus() {
  const conn = resolveGatewayConnection();
  const healthUrl = `${conn.baseUrl}/healthz`;
  try {
    const res = await fetch(healthUrl, { signal: AbortSignal.timeout(3000) });
    const text = await res.text();
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { raw: text };
    }
    if (!res.ok) {
      console.error(JSON.stringify({ ok: false, status: res.status, healthUrl, payload }, null, 2));
      process.exit(1);
    }
    console.log(
      JSON.stringify(
        {
          ok: true,
          version: VERSION,
          healthUrl,
          runtimeFound: conn.runtimeFound,
          tokenConfigured: conn.clientToken.length >= 16,
          gateway: payload,
        },
        null,
        2
      )
    );
  } catch (err) {
    console.error(
      `nidoagent: không kết nối được tới NidoAI Gateway (${healthUrl}): ${
        err instanceof Error ? err.message : String(err)
      }`
    );
    console.error('Gợi ý: chạy `systemctl --user start nidoai-gateway` hoặc `nidoai serve`.');
    process.exit(1);
  }
}

function readAttachedFile(filePath) {
  const resolved = path.resolve(filePath);
  const stat = fs.statSync(resolved);
  if (!stat.isFile()) {
    throw new Error(`Tệp đính kèm không hợp lệ (không phải tệp thông thường): ${resolved}`);
  }
  const ext = path.extname(resolved).toLowerCase();
  if (OFFICE_EXTS.has(ext)) {
    const officeBin = process.env.NIDOOFFICE_BIN || 'nidooffice';
    const extracted = execFileSync(officeBin, ['read', resolved], {
      encoding: 'utf8',
      maxBuffer: 4 * 1024 * 1024,
      env: process.env,
    });
    const rawBuf = Buffer.from(extracted, 'utf8');
    const truncated = rawBuf.length > MAX_ATTACH_BYTES;
    const text = rawBuf.subarray(0, MAX_ATTACH_BYTES).toString('utf8');
    return [
      `--- File: ${resolved} (extracted via nidooffice${truncated ? ', truncated to 64 KiB' : ''}) ---`,
      text,
      '--- End of file ---',
    ].join('\n');
  }
  const raw = fs.readFileSync(resolved);
  const truncated = raw.length > MAX_ATTACH_BYTES;
  const text = raw.subarray(0, MAX_ATTACH_BYTES).toString('utf8');
  return [
    `--- File: ${resolved} (${stat.size} bytes${truncated ? ', truncated to 64 KiB' : ''}) ---`,
    text,
    '--- End of file ---',
  ].join('\n');
}

async function sendChatCompletion(conn, messages) {
  if (!conn.clientToken || conn.clientToken.length < 16) {
    throw new Error(
      'Chưa có NIDOAI_CLIENT_TOKEN. Hãy mở Trung tâm NidoHub → Thiết lập AI hoặc chạy `nidoai serve` trước.'
    );
  }
  const res = await fetch(`${conn.baseUrl}/v1/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${conn.clientToken}`,
    },
    body: JSON.stringify({
      model: process.env.NIDO_MODEL || 'auto',
      messages,
      stream: false,
    }),
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data?.error?.message || `HTTP ${res.status}`;
    throw new Error(`Lỗi NidoAI Gateway: ${msg}`);
  }
  const content = data?.choices?.[0]?.message?.content;
  if (typeof content !== 'string') {
    throw new Error('Phản hồi từ Gateway không chứa choices[0].message.content');
  }
  return content;
}

async function cmdAsk(args) {
  const files = [];
  const promptParts = [];
  let outPath = '';

  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === '--file' || args[i] === '-f') {
      i += 1;
      if (!args[i]) {
        console.error('nidoagent ask: thiếu đường dẫn sau --file');
        process.exit(2);
      }
      files.push(args[i]);
    } else if (args[i] === '--out' || args[i] === '-o') {
      i += 1;
      if (!args[i]) {
        console.error('nidoagent ask: thiếu đường dẫn sau --out');
        process.exit(2);
      }
      outPath = args[i];
    } else {
      promptParts.push(args[i]);
    }
  }

  const prompt = promptParts.join(' ').trim();
  if (!prompt && files.length === 0) {
    console.error('Usage: nidoagent ask [--file <path>] [--out <path>] <prompt>');
    process.exit(2);
  }

  const sections = [];
  for (const f of files) {
    sections.push(readAttachedFile(f));
  }
  sections.push(prompt || 'Hãy tóm tắt và phân tích nội dung các tệp đính kèm.');

  const outExt = outPath ? path.extname(outPath).toLowerCase() : '';
  let systemPrompt =
    'Bạn là NidoAgent — trợ lý điều phối công việc mặc định trên hệ điều hành Ubuntu-Nido. Trả lời ngắn gọn, chính xác, ưu tiên tiếng Việt khi người dùng hỏi bằng tiếng Việt.';
  if (outExt === '.xlsx') {
    systemPrompt +=
      ' Khi xuất báo cáo bảng tính (.xlsx), hãy trình bày dữ liệu chính dưới dạng bảng Markdown (| Cột | Giá trị |) để hệ thống trích xuất sang Excel chính xác.';
  }

  const conn = resolveGatewayConnection();
  const reply = await sendChatCompletion(conn, [
    {
      role: 'system',
      content: systemPrompt,
    },
    { role: 'user', content: sections.join('\n\n') },
  ]);

  if (outPath) {
    const resolvedOut = path.resolve(outPath);
    fs.mkdirSync(path.dirname(resolvedOut), { recursive: true });
    if (outExt === '.docx' || outExt === '.xlsx') {
      const officeBin = process.env.NIDOOFFICE_BIN || 'nidooffice';
      execFileSync(officeBin, ['create', '--type', outExt.slice(1), '--out', resolvedOut, '--from', '-'], {
        input: reply,
        encoding: 'utf8',
        env: process.env,
      });
    } else {
      fs.writeFileSync(resolvedOut, `${reply}\n`, 'utf8');
    }
    console.log(reply);
    console.log(`\n[Đã xuất báo cáo: ${resolvedOut}]`);
    return;
  }

  console.log(reply);
}

async function cmdChat() {
  const conn = resolveGatewayConnection();
  const messages = [
    {
      role: 'system',
      content:
        'Bạn là NidoAgent — trợ lý điều phối công việc trên Ubuntu-Nido. Trả lời rõ ràng, hữu ích.',
    },
  ];
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: 'nidoagent> ',
  });
  console.log(`Ubuntu-Nido Agent Chat (${conn.baseUrl}) — gõ "exit" để thoát.`);
  rl.prompt();

  for await (const line of rl) {
    const trimmed = line.trim();
    if (!trimmed) {
      rl.prompt();
      continue;
    }
    if (trimmed === 'exit' || trimmed === 'quit' || trimmed === '/exit') {
      break;
    }
    messages.push({ role: 'user', content: trimmed });
    try {
      const reply = await sendChatCompletion(conn, messages);
      messages.push({ role: 'assistant', content: reply });
      console.log(`\n${reply}\n`);
    } catch (err) {
      messages.pop();
      console.error(`Lỗi: ${err instanceof Error ? err.message : String(err)}`);
    }
    rl.prompt();
  }
  rl.close();
}

async function main() {
  const [, , cmd, ...rest] = process.argv;
  if (!cmd || cmd === '--help' || cmd === '-h' || cmd === 'help') {
    printHelp();
    return;
  }
  if (cmd === '--version' || cmd === '-v' || cmd === 'version') {
    console.log(`nidoagent ${VERSION}`);
    return;
  }
  if (cmd === 'status' || cmd === 'doctor') {
    await cmdStatus();
    return;
  }
  if (cmd === 'ask') {
    await cmdAsk(rest);
    return;
  }
  if (cmd === 'chat') {
    await cmdChat();
    return;
  }

  console.error(`Lệnh không hỗ trợ: ${cmd}. Chạy 'nidoagent --help'.`);
  process.exit(1);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
