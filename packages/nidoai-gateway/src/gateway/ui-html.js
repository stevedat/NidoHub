import { PKG_VERSION } from '../config.js';
export function renderControlPanelHtml(csrfToken, version = PKG_VERSION) {
    return `<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="nidoai-csrf" content="${csrfToken}" />
  <title>NidoAI Control Plane</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #090d14;
      --surface: #111722;
      --surface-elevated: #161f2e;
      --border: #233044;
      --border-focus: #38bdf8;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --accent: #0ea5e9;
      --accent-hover: #0284c7;
      --ok-bg: #064e3b;
      --ok-text: #6ee7b7;
      --warn-bg: #78350f;
      --warn-text: #fcd34d;
      --err-bg: #7f1d1d;
      --err-text: #fca5a5;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      font-size: 14px;
      line-height: 1.5;
      font-variant-numeric: tabular-nums;
    }
    .shell {
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px 20px 56px;
    }
    header {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 20px;
    }
    .brand-title {
      display: flex;
      align-items: center;
      gap: 12px;
      margin: 0;
      font-size: 20px;
      font-weight: 700;
      letter-spacing: -0.01em;
    }
    .brand-sub {
      margin: 4px 0 0;
      color: var(--text-muted);
      font-size: 13px;
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px;
      margin-bottom: 24px;
    }
    .summary-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 14px 16px;
    }
    .summary-label {
      color: var(--text-muted);
      font-size: 12px;
      font-weight: 500;
      margin-bottom: 6px;
    }
    .summary-value {
      font-family: var(--mono);
      font-size: 16px;
      font-weight: 600;
      color: var(--text);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .summary-meta {
      margin-top: 6px;
      font-size: 12px;
      color: var(--text-muted);
      font-family: var(--mono);
    }
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: 4px;
      font-family: var(--mono);
      font-size: 11px;
      font-weight: 600;
    }
    .badge-ok { background: var(--ok-bg); color: var(--ok-text); }
    .badge-warn { background: var(--warn-bg); color: var(--warn-text); }
    .badge-err { background: var(--err-bg); color: var(--err-text); }
    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 20px;
    }
    .tab-btn {
      min-height: 44px;
      padding: 0 16px;
      background: transparent;
      color: var(--text-muted);
      border: none;
      border-bottom: 2px solid transparent;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
    }
    .tab-btn:hover { color: var(--text); }
    .tab-btn.active {
      color: var(--border-focus);
      border-bottom-color: var(--border-focus);
    }
    .panel { display: none; }
    .panel.active { display: block; }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 18px 20px;
      margin-bottom: 18px;
    }
    .card-header {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }
    .card-title {
      margin: 0;
      font-size: 15px;
      font-weight: 600;
    }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-bottom: 12px;
    }
    .field label {
      font-size: 13px;
      font-weight: 500;
      color: var(--text);
    }
    input, select, textarea {
      min-height: 44px;
      padding: 10px 12px;
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 5px;
      font-family: var(--mono);
      font-size: 13px;
    }
    textarea {
      min-height: 110px;
      resize: vertical;
    }
    input:focus, select:focus, textarea:focus {
      outline: none;
      border-color: var(--border-focus);
    }
    .btn {
      min-height: 44px;
      padding: 0 16px;
      border-radius: 5px;
      border: 1px solid var(--border);
      background: var(--surface-elevated);
      color: var(--text);
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }
    .btn:hover { border-color: var(--text-muted); }
    .btn-primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    .btn-primary:hover { background: var(--accent-hover); }
    .btn-danger {
      background: var(--err-bg);
      border-color: #991b1b;
      color: var(--err-text);
    }
    .btn-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 8px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      text-align: left;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
    }
    th {
      color: var(--text-muted);
      font-weight: 600;
      background: var(--surface-elevated);
    }
    td.mono { font-family: var(--mono); }
    pre.output-box {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 5px;
      padding: 14px;
      font-family: var(--mono);
      font-size: 12.5px;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 360px;
      overflow-y: auto;
      margin: 12px 0 0;
    }
    .toast {
      position: fixed;
      bottom: 20px;
      right: 20px;
      background: var(--surface-elevated);
      border: 1px solid var(--border-focus);
      color: var(--text);
      padding: 12px 16px;
      border-radius: 6px;
      font-size: 13px;
      display: none;
      z-index: 50;
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1 class="brand-title">
          <span data-i18n="header.title"></span>
          <span class="badge badge-ok">v${version}</span>
        </h1>
        <p class="brand-sub" data-i18n="header.subtitle"></p>
      </div>
      <div class="header-actions">
        <button class="btn" id="btn-refresh" data-i18n="actions.refresh"></button>
        <button class="btn" id="btn-lang">EN / VI</button>
      </div>
    </header>

    <!-- Pinned Top Summary Cards -->
    <section class="summary-grid" aria-label="Summary metrics">
      <div class="summary-card">
        <div class="summary-label" data-i18n="summary.gateway"></div>
        <div class="summary-value">
          <span id="sum-bind">127.0.0.1:18090</span>
          <span class="badge badge-ok" id="sum-discovery-badge">Auto-pair</span>
        </div>
        <div class="summary-meta" id="sum-discovery-path">~/.nidoai/runtime.json</div>
      </div>
      <div class="summary-card">
        <div class="summary-label" data-i18n="summary.track1"></div>
        <div class="summary-value">
          <span id="sum-primary-backend">gemini</span>
          <span class="badge badge-warn" id="sum-primary-status">...</span>
        </div>
        <div class="summary-meta" id="sum-primary-model">gemini-2.5-flash</div>
      </div>
      <div class="summary-card">
        <div class="summary-label" data-i18n="summary.track2"></div>
        <div class="summary-value">
          <span>codex app-server</span>
          <span class="badge badge-ok" id="sum-codex-status">...</span>
        </div>
        <div class="summary-meta" id="sum-codex-thread">No active thread</div>
      </div>
      <div class="summary-card">
        <div class="summary-label" data-i18n="summary.telemetry"></div>
        <div class="summary-value">
          <span id="sum-req-count">0 reqs</span>
          <span class="badge badge-ok" id="sum-last-ttft">TTFT: -</span>
        </div>
        <div class="summary-meta" id="sum-last-total">Total: -</div>
      </div>
    </section>

    <!-- Navigation Tabs -->
    <nav class="tabs" role="tablist">
      <button class="tab-btn active" data-tab="tab-playground" data-i18n="tabs.playground"></button>
      <button class="tab-btn" data-tab="tab-codex" data-i18n="tabs.codex"></button>
      <button class="tab-btn" data-tab="tab-config" data-i18n="tabs.config"></button>
      <button class="tab-btn" data-tab="tab-doctor" data-i18n="tabs.doctor"></button>
    </nav>

    <!-- Tab 1: Track 1 Chat/JSON Playground -->
    <section class="panel active" id="tab-playground">
      <div class="card">
        <div class="card-header">
          <h2 class="card-title" data-i18n="playground.title"></h2>
          <span class="badge badge-ok" id="pg-metrics-badge">POST /v1/chat/completions</span>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="pg-model" data-i18n="playground.model"></label>
            <select id="pg-model">
              <option value="auto">auto (Default primary backend)</option>
              <option value="gemini/gemini-2.5-flash">gemini/gemini-2.5-flash (Pinned)</option>
              <option value="openai/gpt-4o-mini">openai/gpt-4o-mini (Pinned)</option>
              <option value="anthropic/claude-sonnet-4-5">anthropic/claude-sonnet-4-5 (Pinned)</option>
              <option value="local/llama3.2">local/llama3.2 (Pinned)</option>
            </select>
          </div>
          <div class="field">
            <label for="pg-format" data-i18n="playground.format"></label>
            <select id="pg-format">
              <option value="text">text</option>
              <option value="json_object">json_object</option>
            </select>
          </div>
        </div>
        <div class="field">
          <label for="pg-prompt" data-i18n="playground.prompt"></label>
          <textarea id="pg-prompt">Tóm tắt ngắn gọn 2 luồng kiến trúc của NidoAI bằng tiếng Việt.</textarea>
        </div>
        <div class="btn-row">
          <button class="btn btn-primary" id="btn-pg-send" data-i18n="playground.send"></button>
        </div>
        <pre class="output-box" id="pg-output">...</pre>
      </div>
    </section>

    <!-- Tab 2: Track 2 Codex App-Server Worker -->
    <section class="panel" id="tab-codex">
      <div class="card">
        <div class="card-header">
          <h2 class="card-title" data-i18n="codex.title"></h2>
          <span class="badge badge-ok">stdio JSON-RPC (read-only sandbox)</span>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="cx-cwd" data-i18n="codex.cwd"></label>
            <input id="cx-cwd" type="text" value="" placeholder="e.g. /path/to/project" />
          </div>
          <div class="field">
            <label for="cx-thread-id" data-i18n="codex.threadId"></label>
            <input id="cx-thread-id" type="text" placeholder="01a1b8..." />
          </div>
        </div>
        <div class="btn-row" style="margin-bottom: 14px;">
          <button class="btn btn-primary" id="btn-cx-start-thread" data-i18n="codex.startThread"></button>
          <button class="btn" id="btn-cx-resume-thread" data-i18n="codex.resumeThread"></button>
        </div>
        <div class="field">
          <label for="cx-prompt" data-i18n="codex.prompt"></label>
          <textarea id="cx-prompt">Read README.md in this workspace and summarize the two tracks in 3 bullet points.</textarea>
        </div>
        <div class="btn-row">
          <button class="btn btn-primary" id="btn-cx-run-turn" data-i18n="codex.runTurn"></button>
          <button class="btn btn-danger" id="btn-cx-interrupt" data-i18n="codex.interrupt"></button>
        </div>
        <pre class="output-box" id="cx-output">...</pre>
      </div>
    </section>

    <!-- Tab 3: Configuration & 1-Click Pairing -->
    <section class="panel" id="tab-config">
      <div class="card">
        <div class="card-header">
          <h2 class="card-title" data-i18n="pairing.title"></h2>
          <span class="badge badge-ok">~/.nidoai/runtime.json</span>
        </div>
        <p style="margin-top:0; color: var(--text-muted);" data-i18n="pairing.desc"></p>
        <div class="btn-row">
          <button class="btn btn-primary" id="btn-copy-env" data-i18n="pairing.copyEnv"></button>
          <button class="btn" id="btn-copy-curl" data-i18n="pairing.copyCurl"></button>
          <button class="btn" id="btn-rotate-token" data-i18n="pairing.rotateToken"></button>
        </div>
        <pre class="output-box" id="pairing-snippet"></pre>
      </div>

      <div class="card">
        <div class="card-header">
          <h2 class="card-title" data-i18n="config.title"></h2>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="cfg-primary" data-i18n="config.primaryBackend"></label>
            <select id="cfg-primary">
              <option value="gemini">gemini (Google Gemini API)</option>
              <option value="openai">openai (OpenAI Official API)</option>
              <option value="anthropic">anthropic (Anthropic Console API)</option>
              <option value="local">local (Ollama / LM Studio)</option>
            </select>
          </div>
          <div class="field">
            <label for="cfg-failover" data-i18n="config.failoverBackend"></label>
            <select id="cfg-failover">
              <option value="">None (Disabled)</option>
              <option value="gemini">gemini</option>
              <option value="openai">openai</option>
              <option value="anthropic">anthropic</option>
              <option value="local">local</option>
            </select>
          </div>
          <div class="field">
            <label for="cfg-gemini-key" data-i18n="config.geminiKey"></label>
            <input id="cfg-gemini-key" type="password" placeholder="Google AI Studio key..." />
          </div>
          <div class="field">
            <label for="cfg-gemini-model" data-i18n="config.geminiModel"></label>
            <input id="cfg-gemini-model" type="text" value="gemini-2.5-flash" />
          </div>
          <div class="field">
            <label for="cfg-openai-key" data-i18n="config.openaiKey"></label>
            <input id="cfg-openai-key" type="password" placeholder="OpenAI API key..." />
          </div>
          <div class="field">
            <label for="cfg-openai-model" data-i18n="config.openaiModel"></label>
            <input id="cfg-openai-model" type="text" value="gpt-4o-mini" />
          </div>
          <div class="field">
            <label for="cfg-anthropic-key" data-i18n="config.anthropicKey"></label>
            <input id="cfg-anthropic-key" type="password" placeholder="Anthropic Console API key..." />
          </div>
          <div class="field">
            <label for="cfg-anthropic-model" data-i18n="config.anthropicModel"></label>
            <input id="cfg-anthropic-model" type="text" value="claude-sonnet-4-5" />
          </div>
          <div class="field">
            <label for="cfg-local-url" data-i18n="config.localUrl"></label>
            <input id="cfg-local-url" type="text" placeholder="http://127.0.0.1:11434/v1" />
          </div>
          <div class="field">
            <label for="cfg-local-model" data-i18n="config.localModel"></label>
            <input id="cfg-local-model" type="text" value="llama3.2" />
          </div>
        </div>
        <div class="btn-row">
          <button class="btn btn-primary" id="btn-save-cfg" data-i18n="config.save"></button>
        </div>
      </div>
    </section>

    <!-- Tab 4: System Doctor & Operational Logs -->
    <section class="panel" id="tab-doctor">
      <div class="card">
        <div class="card-header">
          <h2 class="card-title" data-i18n="doctor.title"></h2>
          <button class="btn btn-primary" id="btn-run-doctor" data-i18n="doctor.run"></button>
        </div>
        <table>
          <thead>
            <tr>
              <th data-i18n="doctor.colComponent"></th>
              <th data-i18n="doctor.colTrack"></th>
              <th data-i18n="doctor.colStatus"></th>
              <th data-i18n="doctor.colLatency"></th>
              <th data-i18n="doctor.colDetail"></th>
            </tr>
          </thead>
          <tbody id="doctor-tbody"></tbody>
        </table>
      </div>

      <div class="card">
        <div class="card-header">
          <h2 class="card-title" data-i18n="logs.title"></h2>
        </div>
        <table>
          <thead>
            <tr>
              <th data-i18n="logs.colTime"></th>
              <th data-i18n="logs.colPath"></th>
              <th data-i18n="logs.colBackend"></th>
              <th data-i18n="logs.colStatus"></th>
              <th data-i18n="logs.colTtft"></th>
              <th data-i18n="logs.colTotal"></th>
            </tr>
          </thead>
          <tbody id="logs-tbody"></tbody>
        </table>
      </div>
    </section>
  </div>

  <div class="toast" id="toast"></div>

  <script>
    const I18N = {
      vi: {
        'header.title': 'Bảng điều khiển NidoAI',
        'header.subtitle': 'Cổng kết nối AI cục bộ 2 luồng: Sinh văn bản/JSON & Codex Agent Worker',
        'actions.refresh': 'Làm mới trạng thái',
        'summary.gateway': 'Địa chỉ Gateway & Tự động ghép nối',
        'summary.track1': 'Luồng 1: Backend Chat / JSON',
        'summary.track2': 'Luồng 2: Codex Agent Worker',
        'summary.telemetry': 'Số đo vận hành gần nhất',
        'tabs.playground': '1. Thử nghiệm Chat / JSON',
        'tabs.codex': '2. Phiên Codex Agent',
        'tabs.config': '3. Cấu hình & Ghép nối App',
        'tabs.doctor': '4. Kiểm tra Doctor & Nhật ký',
        'playground.title': 'Thử nghiệm Luồng 1 (POST /v1/chat/completions)',
        'playground.model': 'Chọn Model / Provider',
        'playground.format': 'Định dạng đầu ra (response_format)',
        'playground.prompt': 'Nội dung yêu cầu (Prompt)',
        'playground.send': 'Gửi yêu cầu thử nghiệm',
        'codex.title': 'Điều khiển phiên Codex App-Server (Luồng 2)',
        'codex.cwd': 'Thư mục Workspace cô lập',
        'codex.threadId': 'Mã phiên (threadId)',
        'codex.startThread': 'Tạo phiên mới (thread/start)',
        'codex.resumeThread': 'Khôi phục phiên (thread/resume)',
        'codex.prompt': 'Yêu cầu cho Agent trong phiên này',
        'codex.runTurn': 'Chạy lượt tác vụ (turn/start)',
        'codex.interrupt': 'Hủy giữa chừng (turn/interrupt)',
        'pairing.title': 'Ghép nối tự động không cần cấu hình (Zero-Config Auto-Pairing)',
        'pairing.desc': 'Các ứng dụng trên máy (như ai-talk-radio) tự động đọc cổng và token từ ~/.nidoai/runtime.json mà không cần copy thủ công. Bạn cũng có thể sao chép đoạn cấu hình bên dưới nếu dùng cho dự án khác.',
        'pairing.copyEnv': 'Sao chép biến .env',
        'pairing.copyCurl': 'Sao chép lệnh cURL mẫu',
        'pairing.rotateToken': 'Đổi Client Token mới',
        'config.title': 'Cấu hình Backend (Lưu vào .env.local & Áp dụng ngay)',
        'config.primaryBackend': 'Backend chính (Primary)',
        'config.failoverBackend': 'Backend dự phòng (Failover cho model=auto)',
        'config.geminiKey': 'Khóa GEMINI_API_KEY mới (để trống nếu giữ nguyên)',
        'config.geminiModel': 'Model Gemini mặc định',
        'config.openaiKey': 'Khóa OPENAI_API_KEY mới (để trống nếu giữ nguyên)',
        'config.openaiModel': 'Model OpenAI mặc định',
        'config.anthropicKey': 'Khóa ANTHROPIC_API_KEY mới (để trống nếu giữ nguyên)',
        'config.anthropicModel': 'Model Anthropic mặc định',
        'config.localUrl': 'Địa chỉ Local LLM (Ollama / LM Studio)',
        'config.localModel': 'Model Local mặc định',
        'config.save': 'Lưu cấu hình & Áp dụng ngay',
        'doctor.title': 'Kiểm tra sức khỏe hệ thống (System Doctor)',
        'doctor.run': 'Chạy kiểm tra Doctor ngay',
        'doctor.colComponent': 'Thành phần',
        'doctor.colTrack': 'Luồng',
        'doctor.colStatus': 'Trạng thái',
        'doctor.colLatency': 'Độ trễ',
        'doctor.colDetail': 'Chi tiết kiểm chứng',
        'logs.title': 'Nhật ký vận hành (Chỉ lưu metadata, không lưu prompt)',
        'logs.colTime': 'Thời gian',
        'logs.colPath': 'Đường dẫn',
        'logs.colBackend': 'Backend / Model',
        'logs.colStatus': 'Mã HTTP',
        'logs.colTtft': 'TTFT',
        'logs.colTotal': 'Tổng thời gian',
        'toast.saved': 'Đã lưu cấu hình và áp dụng thành công!',
        'toast.copied': 'Đã sao chép vào bộ nhớ tạm!',
        'toast.rotated': 'Đã tạo mới Client Token và cập nhật ~/.nidoai/runtime.json!'
      },
      en: {
        'header.title': 'NidoAI Control Plane',
        'header.subtitle': 'Two-track Local AI Gateway: Text/JSON Generation & Codex Agent Worker',
        'actions.refresh': 'Refresh state',
        'summary.gateway': 'Gateway bind & Auto-discovery',
        'summary.track1': 'Track 1: Chat / JSON Backend',
        'summary.track2': 'Track 2: Codex Agent Worker',
        'summary.telemetry': 'Recent operational telemetry',
        'tabs.playground': '1. Chat / JSON Playground',
        'tabs.codex': '2. Codex Agent Session',
        'tabs.config': '3. Config & App Pairing',
        'tabs.doctor': '4. System Doctor & Logs',
        'playground.title': 'Track 1 Playground (POST /v1/chat/completions)',
        'playground.model': 'Model / Provider selection',
        'playground.format': 'Response format',
        'playground.prompt': 'User prompt',
        'playground.send': 'Send test request',
        'codex.title': 'Codex App-Server Session Control (Track 2)',
        'codex.cwd': 'Isolated workspace directory',
        'codex.threadId': 'Session threadId',
        'codex.startThread': 'Start new thread (thread/start)',
        'codex.resumeThread': 'Resume thread (thread/resume)',
        'codex.prompt': 'Task prompt for agent turn',
        'codex.runTurn': 'Run turn (turn/start)',
        'codex.interrupt': 'Interrupt active turn (turn/interrupt)',
        'pairing.title': 'Zero-Config Local App Auto-Pairing',
        'pairing.desc': 'Local apps (such as ai-talk-radio) automatically discover the gateway URL and token via ~/.nidoai/runtime.json with zero manual setup.',
        'pairing.copyEnv': 'Copy .env snippet',
        'pairing.copyCurl': 'Copy sample cURL',
        'pairing.rotateToken': 'Rotate Client Token',
        'config.title': 'Backend Configuration (Hot-reload .env.local)',
        'config.primaryBackend': 'Primary backend',
        'config.failoverBackend': 'Failover backend (for model=auto)',
        'config.geminiKey': 'New GEMINI_API_KEY (leave blank to keep current)',
        'config.geminiModel': 'Default Gemini model',
        'config.openaiKey': 'New OPENAI_API_KEY (leave blank to keep current)',
        'config.openaiModel': 'Default OpenAI model',
        'config.anthropicKey': 'New ANTHROPIC_API_KEY (leave blank to keep current)',
        'config.anthropicModel': 'Default Anthropic model',
        'config.localUrl': 'Local LLM Base URL (Ollama / LM Studio)',
        'config.localModel': 'Default Local model',
        'config.save': 'Save & Hot-reload Configuration',
        'doctor.title': 'System Readiness Doctor',
        'doctor.run': 'Run Doctor Probes',
        'doctor.colComponent': 'Component',
        'doctor.colTrack': 'Track',
        'doctor.colStatus': 'Status',
        'doctor.colLatency': 'Latency',
        'doctor.colDetail': 'Verification detail',
        'logs.title': 'Operational Telemetry Log (Metadata only)',
        'logs.colTime': 'Time',
        'logs.colPath': 'Route',
        'logs.colBackend': 'Backend / Model',
        'logs.colStatus': 'Status',
        'logs.colTtft': 'TTFT',
        'logs.colTotal': 'Total time',
        'toast.saved': 'Configuration saved and reloaded!',
        'toast.copied': 'Copied to clipboard!',
        'toast.rotated': 'Rotated Client Token and updated ~/.nidoai/runtime.json!'
      }
    };

    let currentLang = 'vi';
    let latestState = null;
    const csrfToken = document.querySelector('meta[name="nidoai-csrf"]')?.getAttribute('content') || '';
    const NL = String.fromCharCode(10);
    const BS = String.fromCharCode(92);

    function t(key) {
      return (I18N[currentLang] && I18N[currentLang][key]) || key;
    }

    function applyI18n() {
      document.querySelectorAll('[data-i18n]').forEach((el) => {
        const key = el.getAttribute('data-i18n');
        if (key) el.textContent = t(key);
      });
    }

    function showToast(msg) {
      const el = document.getElementById('toast');
      el.textContent = msg;
      el.style.display = 'block';
      setTimeout(() => { el.style.display = 'none'; }, 3000);
    }

    async function apiCall(path, method = 'GET', body = undefined) {
      const res = await fetch(path, {
        method,
        headers: {
          'Content-Type': 'application/json',
          'x-nidoai-csrf': csrfToken
        },
        body: body !== undefined ? JSON.stringify(body) : undefined
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.error?.message || ('HTTP ' + res.status));
      }
      return data;
    }

    async function loadState() {
      const state = await apiCall('/api/admin/state');
      latestState = state;
      document.getElementById('sum-bind').textContent = state.bind;
      document.getElementById('sum-discovery-path').textContent = state.discoveryPath;
      document.getElementById('sum-primary-backend').textContent = state.primaryBackend.kind;
      const pStatus = document.getElementById('sum-primary-status');
      const primaryReady = state.primaryBackend.reachable !== undefined
        ? state.primaryBackend.reachable
        : state.primaryBackend.configured;
      pStatus.textContent = primaryReady
        ? 'READY'
        : (state.primaryBackend.configured ? 'OFFLINE' : 'UNCONFIGURED');
      pStatus.className = 'badge ' + (primaryReady ? 'badge-ok' : 'badge-warn');
      document.getElementById('sum-primary-model').textContent = state.primaryBackend.defaultModel;

      const cxStatus = document.getElementById('sum-codex-status');
      cxStatus.textContent = state.codex.running ? 'RUNNING' : 'STANDBY';
      document.getElementById('sum-codex-thread').textContent = state.codex.activeThreadId
        ? ('Thread: ' + state.codex.activeThreadId.slice(0, 14) + '...')
        : 'Ready on demand';

      document.getElementById('sum-req-count').textContent = state.logs.length + ' reqs';
      const lastChatLog = state.logs.find((l) => l.path === '/v1/chat/completions');
      document.getElementById('sum-last-ttft').textContent =
        lastChatLog && lastChatLog.ttftMs !== undefined ? ('TTFT: ' + lastChatLog.ttftMs + ' ms') : 'TTFT: -';
      document.getElementById('sum-last-total').textContent =
        lastChatLog ? ('Total: ' + lastChatLog.totalMs + ' ms') : 'Total: -';

      // Populate form
      document.getElementById('cfg-primary').value = state.primaryBackend.kind;
      document.getElementById('cfg-failover').value = state.failoverBackend || '';
      document.getElementById('cfg-gemini-key').placeholder = state.backends.gemini.maskedKey || 'Google AI Studio key...';
      document.getElementById('cfg-gemini-model').value = state.backends.gemini.defaultModel;
      document.getElementById('cfg-openai-key').placeholder = state.backends.openai.maskedKey || 'OpenAI API key...';
      document.getElementById('cfg-openai-model').value = state.backends.openai.defaultModel;
      if (state.backends.anthropic) {
        document.getElementById('cfg-anthropic-key').placeholder = state.backends.anthropic.maskedKey || 'Anthropic Console key...';
        document.getElementById('cfg-anthropic-model').value = state.backends.anthropic.defaultModel;
      }
      document.getElementById('cfg-local-url').value = state.backends.local.configured ? state.backends.local.baseUrl : '';
      document.getElementById('cfg-local-model').value = state.backends.local.defaultModel;

      // Pairing snippet
      const cwdInput = document.getElementById('cx-cwd');
      if (cwdInput && !cwdInput.value && state.codex && state.codex.cwd) {
        cwdInput.value = state.codex.cwd;
      }

      document.getElementById('pairing-snippet').textContent = [
        '# Auto-discovered via ~/.nidoai/runtime.json, or set in .env:',
        'NIDOAI_ENABLED=1',
        'NIDOAI_BASE_URL=http://' + state.bind,
        'NIDOAI_CLIENT_TOKEN=' + (state.maskedClientToken || '***')
      ].join(NL);

      // Render logs
      const logsTbody = document.getElementById('logs-tbody');
      logsTbody.innerHTML = '';
      for (const log of state.logs.slice(0, 20)) {
        const tr = document.createElement('tr');
        const badgeCls = log.status >= 200 && log.status < 300 ? 'badge-ok' : 'badge-err';
        const cells = [
          [log.timestamp.slice(11, 19), 'mono'],
          [log.method + ' ' + log.path, 'mono'],
          [log.backend ? (log.backend + ' / ' + (log.model || '')) : '-', 'mono'],
          [null, null, log.status, badgeCls],
          [log.ttftMs !== undefined ? (log.ttftMs + ' ms') : '-', 'mono'],
          [log.totalMs + ' ms', 'mono']
        ];
        for (const cell of cells) {
          const td = document.createElement('td');
          if (cell[2] !== undefined) {
            const span = document.createElement('span');
            span.className = 'badge ' + cell[3];
            span.textContent = String(cell[2]);
            td.appendChild(span);
          } else {
            if (cell[1]) td.className = cell[1];
            td.textContent = String(cell[0]);
          }
          tr.appendChild(td);
        }
        logsTbody.appendChild(tr);
      }
    }

    // Tabs switching
    document.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
        document.querySelectorAll('.panel').forEach((p) => p.classList.remove('active'));
        btn.classList.add('active');
        const target = btn.getAttribute('data-tab');
        if (target) document.getElementById(target).classList.add('active');
      });
    });

    document.getElementById('btn-lang').addEventListener('click', () => {
      currentLang = currentLang === 'vi' ? 'en' : 'vi';
      applyI18n();
    });

    document.getElementById('btn-refresh').addEventListener('click', () => loadState());

    // Track 1 Playground
    document.getElementById('btn-pg-send').addEventListener('click', async () => {
      const out = document.getElementById('pg-output');
      const badge = document.getElementById('pg-metrics-badge');
      out.textContent = 'Running...';
      try {
        const model = document.getElementById('pg-model').value;
        const format = document.getElementById('pg-format').value;
        const prompt = document.getElementById('pg-prompt').value;
        const res = await apiCall('/api/admin/playground/chat', 'POST', {
          model,
          responseFormat: format,
          prompt
        });
        badge.textContent = 'backend=' + res.backend + ' | TTFT=' + res.ttftMs + 'ms | Total=' + res.totalMs + 'ms';
        badge.className = 'badge badge-ok';
        out.textContent = res.content;
        await loadState();
      } catch (err) {
        badge.textContent = 'Error';
        badge.className = 'badge badge-err';
        out.textContent = 'Error: ' + err.message;
        await loadState();
      }
    });

    // Track 2 Codex controls
    document.getElementById('btn-cx-start-thread').addEventListener('click', async () => {
      const out = document.getElementById('cx-output');
      out.textContent = 'Starting thread...';
      try {
        const cwd = document.getElementById('cx-cwd').value;
        const res = await apiCall('/api/admin/codex/thread/start', 'POST', { cwd });
        document.getElementById('cx-thread-id').value = res.threadId;
        out.textContent = JSON.stringify(res, null, 2);
        await loadState();
      } catch (err) {
        out.textContent = 'Error: ' + err.message;
      }
    });

    document.getElementById('btn-cx-resume-thread').addEventListener('click', async () => {
      const out = document.getElementById('cx-output');
      out.textContent = 'Resuming thread...';
      try {
        const cwd = document.getElementById('cx-cwd').value;
        const threadId = document.getElementById('cx-thread-id').value;
        const res = await apiCall('/api/admin/codex/thread/resume', 'POST', { cwd, threadId });
        out.textContent = JSON.stringify(res, null, 2);
        await loadState();
      } catch (err) {
        out.textContent = 'Error: ' + err.message;
      }
    });

    document.getElementById('btn-cx-run-turn').addEventListener('click', async () => {
      const out = document.getElementById('cx-output');
      out.textContent = 'Running Codex turn...';
      try {
        const cwd = document.getElementById('cx-cwd').value;
        const threadId = document.getElementById('cx-thread-id').value;
        const text = document.getElementById('cx-prompt').value;
        const res = await apiCall('/api/admin/codex/turn/run', 'POST', { cwd, threadId, text });
        document.getElementById('cx-thread-id').value = res.threadId;
        out.textContent =
          '[Status: ' + res.status + ' | TTFT: ' + res.ttftMs + ' ms | Total: ' + res.totalMs + ' ms]' + NL + NL +
          res.text;
        await loadState();
      } catch (err) {
        out.textContent = 'Error: ' + err.message;
      }
    });

    document.getElementById('btn-cx-interrupt').addEventListener('click', async () => {
      const out = document.getElementById('cx-output');
      try {
        const threadId = document.getElementById('cx-thread-id').value;
        const res = await apiCall('/api/admin/codex/turn/interrupt', 'POST', { threadId });
        out.textContent = JSON.stringify(res, null, 2);
        await loadState();
      } catch (err) {
        out.textContent = 'Error: ' + err.message;
      }
    });

    // Save config
    document.getElementById('btn-save-cfg').addEventListener('click', async () => {
      const failoverVal = document.getElementById('cfg-failover').value;
      await apiCall('/api/admin/config', 'POST', {
        primaryBackend: document.getElementById('cfg-primary').value,
        failoverBackend: failoverVal,
        allowCrossProviderFailover: Boolean(failoverVal),
        geminiApiKey: document.getElementById('cfg-gemini-key').value,
        geminiModel: document.getElementById('cfg-gemini-model').value,
        openaiApiKey: document.getElementById('cfg-openai-key').value,
        openaiModel: document.getElementById('cfg-openai-model').value,
        anthropicApiKey: document.getElementById('cfg-anthropic-key').value,
        anthropicModel: document.getElementById('cfg-anthropic-model').value,
        localBaseUrl: document.getElementById('cfg-local-url').value,
        localModel: document.getElementById('cfg-local-model').value
      });
      document.getElementById('cfg-gemini-key').value = '';
      document.getElementById('cfg-openai-key').value = '';
      document.getElementById('cfg-anthropic-key').value = '';
      await loadState();
      showToast(t('toast.saved'));
    });

    // Copy helpers & token rotation
    document.getElementById('btn-copy-env').addEventListener('click', async () => {
      if (!latestState) return;
      let token = '<SET_YOUR_TOKEN>';
      try {
        const pt = await apiCall('/api/admin/pairing-token');
        if (pt && pt.pairingToken) token = pt.pairingToken;
      } catch {}
      navigator.clipboard.writeText([
        'NIDOAI_ENABLED=1',
        'NIDOAI_BASE_URL=http://' + latestState.bind,
        'NIDOAI_CLIENT_TOKEN=' + token
      ].join(NL));
      showToast(t('toast.copied'));
    });

    document.getElementById('btn-copy-curl').addEventListener('click', async () => {
      if (!latestState) return;
      let token = '<SET_YOUR_TOKEN>';
      try {
        const pt = await apiCall('/api/admin/pairing-token');
        if (pt && pt.pairingToken) token = pt.pairingToken;
      } catch {}
      const samplePayload = JSON.stringify({
        model: 'auto',
        messages: [{ role: 'user', content: 'Hello NidoAI' }]
      });
      const cmd = [
        'curl -s http://' + latestState.bind + '/v1/chat/completions',
        '  -H "Content-Type: application/json"',
        '  -H "Authorization: Bearer ' + token + '"',
        "  -d '" + samplePayload + "'"
      ].join(' ' + BS + NL);
      navigator.clipboard.writeText(cmd);
      showToast(t('toast.copied'));
    });

    document.getElementById('btn-rotate-token').addEventListener('click', async () => {
      await apiCall('/api/admin/rotate-token', 'POST', {});
      await loadState();
      showToast(t('toast.rotated'));
    });

    // Run Doctor
    async function triggerDoctor() {
      const tbody = document.getElementById('doctor-tbody');
      tbody.innerHTML = '<tr><td colspan="5">Running probes...</td></tr>';
      const res = await apiCall('/api/admin/doctor', 'POST', {});
      tbody.innerHTML = '';
      for (const r of res.checks) {
        const badgeCls =
          r.status === 'PASS' ? 'badge-ok' : r.status === 'PARTIAL' || r.status === 'UNCONFIGURED' ? 'badge-warn' : 'badge-err';
        const tr = document.createElement('tr');
        const dcells = [
          [r.name, 'mono'],
          [r.track, 'mono'],
          [null, null, r.status, badgeCls],
          [r.latencyMs !== undefined ? (r.latencyMs + ' ms') : '-', 'mono'],
          [r.detail, '']
        ];
        for (const cell of dcells) {
          const td = document.createElement('td');
          if (cell[2] !== undefined) {
            const span = document.createElement('span');
            span.className = 'badge ' + cell[3];
            span.textContent = String(cell[2]);
            td.appendChild(span);
          } else {
            if (cell[1]) td.className = cell[1];
            td.textContent = String(cell[0]);
          }
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
    }

    document.getElementById('btn-run-doctor').addEventListener('click', () => triggerDoctor());

    applyI18n();
    loadState();
  </script>
</body>
</html>`;
}
