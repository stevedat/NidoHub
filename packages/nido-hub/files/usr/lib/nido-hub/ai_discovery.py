#!/usr/bin/env python3
"""Hardware-aware AI discovery, live verification, and local Ollama onboarding for NidoHub."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

OLLAMA_RELEASES_API = "https://api.github.com/repos/ollama/ollama/releases/latest"
OLLAMA_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"

VISION_MODEL_PATTERNS = (
    "gemma3",
    "qwen2.5vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "qwen3vl",
    "llama3.2-vision",
    "llama4",
    "llava",
    "minicpm-v",
    "moondream",
    "pixtral",
    "internvl",
    "bakllava",
    "mistral-small3.1",
)

ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True)
class HardwareProfile:
    arch: str
    cpu_cores: int
    ram_gb: float
    free_disk_gb: float
    gpu_name: str
    gpu_vram_gb: float
    tier: str  # "cloud-first" | "hybrid" | "local-pro"


@dataclass(frozen=True)
class RecommendedLocalModel:
    id: str
    label: str
    size_gb: float
    min_ram_gb: float
    supports_vision: bool
    recommended: bool
    fits_hardware: bool
    reason_vi: str


def normalize_linux_arch(raw_arch: str | None = None) -> str:
    val = (raw_arch or platform.machine() or "").strip().lower()
    if val in ("x86_64", "amd64"):
        return "amd64"
    if val in ("aarch64", "arm64"):
        return "arm64"
    return val or "amd64"


def is_vision_model(model_name: str) -> bool:
    low = model_name.strip().lower()
    if not low:
        return False
    # gemma3:1b is text-only; gemma3:4b/12b/27b are multimodal vision-capable
    if low.startswith("gemma3:1b"):
        return False
    return any(tok in low for tok in VISION_MODEL_PATTERNS)


def classify_hardware_tier(ram_gb: float, gpu_vram_gb: float, free_disk_gb: float) -> str:
    if free_disk_gb < 6.0:
        return "cloud-first"
    if gpu_vram_gb >= 10.0 or ram_gb >= 22.0:
        return "local-pro"
    if gpu_vram_gb >= 4.0 or ram_gb >= 7.5:
        return "hybrid"
    return "cloud-first"


def _read_ram_gb() -> float:
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        try:
            for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024 * 1024), 1)
        except Exception:
            pass
    if shutil.which("sysctl"):
        try:
            res = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip().isdigit():
                return round(int(res.stdout.strip()) / (1024**3), 1)
        except Exception:
            pass
    return 4.0


def _detect_gpu() -> tuple[str, float]:
    # 1. NVIDIA GPU via nvidia-smi (exact VRAM)
    if shutil.which("nvidia-smi"):
        try:
            res = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2.5,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                line = res.stdout.strip().splitlines()[0]
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    vram_gb = round(float(parts[1]) / 1024.0, 1)
                    return parts[0], vram_gb
        except Exception:
            pass

    # 2. PCI GPU check via lspci (AMD / Intel / virtual GPU)
    if shutil.which("lspci"):
        try:
            res = subprocess.run(
                ["lspci"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    low = line.lower()
                    if any(k in low for k in ("vga compatible", "3d controller", "display controller")):
                        desc = line.split(":", 2)[-1].strip()
                        if any(v in low for v in ("virtio", "qxl", "bochs", "vmware", "virtualbox")):
                            return f"{desc} (VM)", 0.0
                        return desc[:64], 0.0
        except Exception:
            pass

    return "", 0.0


def detect_hardware(home_dir: Path | None = None) -> HardwareProfile:
    target_dir = home_dir or Path.home()
    ram_gb = _read_ram_gb()
    cores = os.cpu_count() or 2
    try:
        usage = shutil.disk_usage(str(target_dir if target_dir.exists() else Path("/")))
        free_gb = round(usage.free / (1024**3), 1)
    except OSError:
        free_gb = 10.0
    gpu_name, gpu_vram_gb = _detect_gpu()
    tier = classify_hardware_tier(ram_gb, gpu_vram_gb, free_gb)
    return HardwareProfile(
        arch=normalize_linux_arch(),
        cpu_cores=cores,
        ram_gb=ram_gb,
        free_disk_gb=free_gb,
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram_gb,
        tier=tier,
    )


def resolve_ollama_binary(home_dir: Path | None = None) -> str | None:
    home = home_dir or Path.home()
    candidates = [
        home / ".local" / "bin" / "ollama",
        home / ".local" / "share" / "nido" / "ollama" / "bin" / "ollama",
        Path("/usr/local/bin/ollama"),
        Path("/usr/bin/ollama"),
    ]
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return shutil.which("ollama")


def _http_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 0.7,
) -> tuple[int, Any, str]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "NidoHub-AI/1.0", **(headers or {})},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw), ""
            except Exception:
                return resp.status, None, raw[:180]
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return exc.code, None, body or str(exc)
    except Exception as exc:
        return 0, None, str(exc)


def probe_local_ai_runtimes(
    home_dir: Path | None = None,
    http_getter: Callable[[str, dict[str, str] | None, float], tuple[int, Any, str]] | None = None,
) -> dict[str, Any]:
    """Detect Ollama, LM Studio, and llama.cpp locally with real model inventory."""
    getter = http_getter or _http_json
    ollama_bin = resolve_ollama_binary(home_dir)

    runtimes: list[dict[str, Any]] = []
    ollama_models: list[str] = []
    ollama_running = False

    # 1. Ollama (127.0.0.1:11434)
    st, data, _ = getter("http://127.0.0.1:11434/api/tags", None, 0.6)
    if st == 200 and isinstance(data, dict):
        ollama_running = True
        for m in data.get("models", []):
            if isinstance(m, dict) and isinstance(m.get("name"), str) and m["name"].strip():
                ollama_models.append(m["name"].strip())
    if ollama_running:
        ol_state = "ready" if ollama_models else "installed-no-models"
    elif ollama_bin:
        ol_state = "installed-not-running"
    else:
        ol_state = "not-installed"
    runtimes.append(
        {
            "id": "ollama",
            "name": "Ollama",
            "baseUrl": OLLAMA_DEFAULT_BASE_URL,
            "binaryPath": ollama_bin or "",
            "installed": bool(ollama_bin) or ollama_running,
            "running": ollama_running,
            "state": ol_state,
            "models": ollama_models,
            "visionModels": [m for m in ollama_models if is_vision_model(m)],
        }
    )

    # 2. Other local OpenAI-compatible runtimes (LM Studio, llama.cpp)
    for rid, name, base_url in (
        ("lmstudio", "LM Studio", "http://127.0.0.1:1234/v1"),
        ("llamacpp", "llama.cpp Server", "http://127.0.0.1:8080/v1"),
    ):
        st_o, data_o, _ = getter(f"{base_url}/models", None, 0.45)
        if st_o == 200 and isinstance(data_o, dict):
            models = [
                str(m["id"]).strip()
                for m in data_o.get("data", [])
                if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"].strip()
            ]
            runtimes.append(
                {
                    "id": rid,
                    "name": name,
                    "baseUrl": base_url,
                    "binaryPath": "",
                    "installed": True,
                    "running": True,
                    "state": "ready" if models else "installed-no-models",
                    "models": models,
                    "visionModels": [m for m in models if is_vision_model(m)],
                }
            )

    all_models: list[str] = []
    all_vision: list[str] = []
    active_runtime = None
    for rt in runtimes:
        if rt["running"] and rt["models"]:
            if active_runtime is None:
                active_runtime = rt
            all_models.extend(rt["models"])
            all_vision.extend(rt["visionModels"])

    return {
        "ollama": runtimes[0],
        "runtimes": runtimes,
        "activeRuntime": active_runtime,
        "installedModels": all_models,
        "visionModels": all_vision,
        "hasReadyLocalModel": bool(all_models),
        "hasVisionLocalModel": bool(all_vision),
    }


def recommend_local_models(hw: HardwareProfile) -> list[RecommendedLocalModel]:
    """Return hardware-calibrated Ollama models with clear RAM/disk fit."""
    specs = [
        (
            "gemma3:4b",
            "Gemma 3 4B (Vision OCR + Tiếng Việt)",
            3.3,
            8.0,
            True,
            "Nhẹ, hỗ trợ cả đọc ảnh OCR cho NidoBox lẫn soạn thảo/tóm tắt NidoOffice.",
        ),
        (
            "qwen2.5:1.5b",
            "Qwen 2.5 1.5B (Siêu nhẹ · Chỉ văn bản)",
            1.0,
            4.0,
            False,
            "Dành cho máy RAM thấp (4–8 GB) khi cần chạy hoàn toàn offline (chỉ xử lý chữ).",
        ),
        (
            "qwen2.5:7b",
            "Qwen 2.5 7B (Tiếng Việt & Lập trình tốt)",
            4.7,
            12.0,
            False,
            "Chất lượng tiếng Việt & code tốt trên máy 12–16 GB RAM hoặc GPU 6–8 GB VRAM.",
        ),
        (
            "qwen2.5vl:7b",
            "Qwen 2.5 VL 7B (Vision OCR chuyên sâu)",
            6.0,
            16.0,
            True,
            "Nhận diện văn bản ảnh/tài liệu tiếng Việt độ chính xác cao 100% offline.",
        ),
        (
            "gemma3:12b",
            "Gemma 3 12B (Vision + Suy luận mạnh)",
            8.1,
            22.0,
            True,
            "Phù hợp máy trạm >= 24 GB RAM hoặc GPU >= 12 GB VRAM.",
        ),
    ]
    effective_mem = max(hw.ram_gb, hw.gpu_vram_gb * 1.4)
    out: list[RecommendedLocalModel] = []
    for mid, label, size_gb, min_ram, vision, reason in specs:
        fits = effective_mem >= (min_ram - 0.5) and hw.free_disk_gb >= (size_gb + 3.0)
        if hw.tier == "local-pro":
            rec = mid in ("qwen2.5vl:7b", "gemma3:12b")
        elif hw.tier == "hybrid":
            rec = mid in ("gemma3:4b", "qwen2.5:7b")
        else:
            rec = mid == "qwen2.5:1.5b" and fits
        out.append(
            RecommendedLocalModel(
                id=mid,
                label=label,
                size_gb=size_gb,
                min_ram_gb=min_ram,
                supports_vision=vision,
                recommended=rec,
                fits_hardware=fits,
                reason_vi=reason,
            )
        )
    out.sort(key=lambda m: (not m.recommended, not m.fits_hardware, m.size_gb))
    return out


def live_verify_backend(
    kind: str,
    api_key: str = "",
    local_base_url: str = "",
    local_model: str = "",
    http_getter: Callable[[str, dict[str, str] | None, float], tuple[int, Any, str]] | None = None,
) -> dict[str, Any]:
    """Perform a lightweight, zero-token live check before declaring a backend ready."""
    getter = http_getter or _http_json
    k = kind.strip().lower()

    if k == "local":
        base = (local_base_url.strip() or OLLAMA_DEFAULT_BASE_URL).rstrip("/")
        models: list[str] = []
        # Try /api/tags first when pointing to Ollama /v1
        if base.endswith("/v1"):
            tags_url = base[:-3] + "/api/tags"
            st_t, data_t, _ = getter(tags_url, None, 2.0)
            if st_t == 200 and isinstance(data_t, dict):
                models = [
                    str(m["name"]).strip()
                    for m in data_t.get("models", [])
                    if isinstance(m, dict) and isinstance(m.get("name"), str) and m["name"].strip()
                ]
        if not models:
            st_m, data_m, err_m = getter(f"{base}/models", None, 2.0)
            if st_m != 200:
                return {
                    "ok": False,
                    "kind": "local",
                    "models": [],
                    "selectedModel": "",
                    "visionReady": False,
                    "message": f"Không kết nối được máy chủ AI cục bộ ({base}). Hãy bật Ollama trước.",
                }
            if isinstance(data_m, dict):
                models = [
                    str(m["id"]).strip()
                    for m in data_m.get("data", [])
                    if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"].strip()
                ]
        if not models:
            return {
                "ok": False,
                "kind": "local",
                "models": [],
                "selectedModel": "",
                "visionReady": False,
                "message": "Ollama đã chạy nhưng chưa tải model nào (0 model). Hãy bấm tải model bên dưới.",
            }
        wanted = local_model.strip()
        if wanted and wanted in models:
            selected = wanted
        elif wanted and any(m.startswith(wanted) for m in models):
            selected = next(m for m in models if m.startswith(wanted))
        else:
            # Prefer a vision-capable model if available, else first installed model
            selected = next((m for m in models if is_vision_model(m)), models[0])
        vision_ok = is_vision_model(selected) or any(is_vision_model(m) for m in models)
        vision_note = "hỗ trợ Vision OCR" if vision_ok else "chỉ xử lý văn bản (chưa có model Vision)"
        return {
            "ok": True,
            "kind": "local",
            "models": models,
            "selectedModel": selected,
            "visionReady": vision_ok,
            "message": f"AI cục bộ sẵn sàng ({len(models)} model · đang chọn {selected} · {vision_note}).",
        }

    key = api_key.strip()
    if not key:
        return {
            "ok": False,
            "kind": k,
            "models": [],
            "selectedModel": "",
            "visionReady": False,
            "message": f"Chưa nhập khóa API cho {k}.",
        }

    if k == "gemini":
        st, _, err = getter(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
            {"x-goog-api-key": key},
            4.5,
        )
        default_m = "gemini-2.5-flash"
    elif k == "openai":
        st, _, err = getter(
            "https://api.openai.com/v1/models",
            {"Authorization": f"Bearer {key}"},
            4.5,
        )
        default_m = "gpt-4o-mini"
    elif k == "anthropic":
        st, _, err = getter(
            "https://api.anthropic.com/v1/models",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            4.5,
        )
        default_m = "claude-sonnet-4-20250514"
    else:
        return {
            "ok": False,
            "kind": k,
            "models": [],
            "selectedModel": "",
            "visionReady": False,
            "message": f"Nhà cung cấp không hỗ trợ: {k}",
        }

    if st == 200:
        return {
            "ok": True,
            "kind": k,
            "models": [default_m],
            "selectedModel": default_m,
            "visionReady": True,
            "message": f"Xác thực {k.upper()} thành công (sẵn sàng Text + Vision OCR · mặc định {default_m}).",
        }
    if st in (401, 403) or (st == 400 and "API_KEY_INVALID" in err):
        return {
            "ok": False,
            "kind": k,
            "models": [],
            "selectedModel": "",
            "visionReady": False,
            "message": f"Khóa API {k.upper()} không hợp lệ hoặc bị thu hồi (HTTP {st}).",
        }
    if st == 429:
        return {
            "ok": True,
            "kind": k,
            "models": [default_m],
            "selectedModel": default_m,
            "visionReady": True,
            "message": f"Khóa {k.upper()} hợp lệ nhưng đang chạm giới hạn tốc độ/quota (HTTP 429).",
        }
    return {
        "ok": False,
        "kind": k,
        "models": [],
        "selectedModel": "",
        "visionReady": False,
        "message": f"Chưa kiểm tra được trực tuyến {k.upper()} (lỗi mạng hoặc HTTP {st}): {err[:100]}",
    }


def compute_capability_matrix(
    gateway_health: dict[str, Any] | None,
    local_info: dict[str, Any],
    agent_states: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Compute exact 3-pillar readiness (NidoBox OCR, NidoOffice AI, NidoAgent)."""
    pb = (gateway_health or {}).get("primaryBackend", {}) if isinstance(gateway_health, dict) else {}
    gw_kind = str(pb.get("kind") or "gemini")
    gw_configured = bool(pb.get("configured", False))
    gw_reachable = bool(pb.get("reachable", False))
    gw_model = str(pb.get("defaultModel") or "")

    if gw_kind == "local":
        has_local_models = bool(local_info.get("hasReadyLocalModel"))
        gw_ready = gw_configured and gw_reachable and has_local_models
        vision_ready = gw_ready and (
            is_vision_model(gw_model) or bool(local_info.get("hasVisionLocalModel"))
        )
    else:
        gw_ready = gw_configured and gw_reachable
        vision_ready = gw_ready and gw_kind in ("gemini", "openai", "anthropic")

    signed_agents = [a for a in agent_states if a.get("signedIn")]
    installed_agents = [a for a in agent_states if a.get("installed")]

    # 1. NidoBox OCR & Vision
    if vision_ready:
        ocr_detail = (
            f"Sẵn sàng qua {gw_kind.upper()} ({gw_model or 'auto'}) — OCR bật theo nhu cầu khi bấm Scan OCR."
        )
    elif gw_ready and gw_kind == "local":
        ocr_detail = (
            "Đang tắt — Ollama hiện chỉ có model văn bản. Tải thêm gemma3:4b hoặc qwen2.5vl:7b để bật Vision OCR."
        )
    elif gw_kind == "local" and local_info.get("ollama", {}).get("running") and not local_info.get("hasReadyLocalModel"):
        ocr_detail = "Đang tắt — Ollama đang chạy nhưng có 0 model. Hãy tải gemma3:4b để bật Vision OCR."
    else:
        ocr_detail = "Đang tắt (mặc định an toàn) — NidoBox OCR cần khóa API ở mục 1 (Gemini miễn phí $0) hoặc model Vision Ollama cục bộ."

    # 2. NidoOffice AI (supports both NidoAI Gateway 127.0.0.1:18090 AND native local Agent CLIs: agy/codex/claude)
    office_agent_names = ", ".join(str(a.get("id", "")).upper() for a in signed_agents)
    office_ready = gw_ready or bool(signed_agents)
    if gw_ready:
        office_detail = f"Sẵn sàng qua NidoAI Gateway (127.0.0.1:18090 · {gw_kind} / {gw_model or 'auto'})" + (
            f" + Agent ({office_agent_names})." if signed_agents else "."
        )
    elif signed_agents:
        office_detail = (
            f"Sẵn sàng qua tài khoản chính chủ ({office_agent_names}) — chọn Antigravity / Codex / Claude ngay trong NidoOffice."
        )
    elif gw_kind == "local" and local_info.get("ollama", {}).get("running") and not local_info.get("hasReadyLocalModel"):
        office_detail = "Chưa sẵn sàng — Ollama có 0 model. Bấm tải model cục bộ ở mục 3."
    else:
        office_detail = "Chưa bật — NidoOffice soạn thảo & ký số vẫn dùng bình thường; đăng nhập Google Antigravity / Codex hoặc nhập khóa API khi cần trợ lý AI."

    # 3. NidoAgent & Coding Agents
    if signed_agents:
        names = ", ".join(str(a.get("id", "")).upper() for a in signed_agents)
        agent_detail = f"Sẵn sàng với tài khoản chính chủ: {names}" + (
            f" + Gateway ({gw_kind})" if gw_ready else ""
        )
    elif gw_ready:
        agent_detail = f"Sẵn sàng qua NidoAI Gateway ({gw_kind}). Có thể đăng nhập thêm Google Antigravity / OpenAI Codex / Claude Code nếu có gói sẵn."
    elif installed_agents:
        names = ", ".join(str(a.get("id", "")) for a in installed_agents)
        agent_detail = f"Đã cài CLI ({names}) nhưng chưa đăng nhập trình duyệt OAuth."
    else:
        agent_detail = "Chưa thiết lập — Bấm Cài & Đăng nhập Google Antigravity (Ultra/Pro/Free) hoặc nhập Gemini Key miễn phí ($0)."

    return {
        "nidobox_ocr": {
            "title": "NidoBox OCR & Vision",
            "ready": vision_ready,
            "badge": "Sẵn sàng" if vision_ready else "Đang tắt",
            "detail": ocr_detail,
        },
        "nidooffice_ai": {
            "title": "NidoOffice AI (Soạn thảo & Tóm tắt)",
            "ready": office_ready,
            "badge": "Sẵn sàng" if office_ready else "Chưa bật",
            "detail": office_detail,
        },
        "nidoagent": {
            "title": "NidoAgent & Trợ lý tác vụ",
            "ready": bool(signed_agents) or gw_ready,
            "badge": "Sẵn sàng" if (bool(signed_agents) or gw_ready) else "Chưa bật",
            "detail": agent_detail,
        },
    }


def build_ai_readiness_report(
    hw: HardwareProfile,
    local_info: dict[str, Any],
    gateway_health: dict[str, Any] | None,
    agent_states: list[dict[str, Any]],
) -> dict[str, Any]:
    caps = compute_capability_matrix(gateway_health, local_info, agent_states)
    rec_models = recommend_local_models(hw)

    gpu_part = (
        f"GPU {hw.gpu_name}" + (f" ({hw.gpu_vram_gb:.1f} GB VRAM)" if hw.gpu_vram_gb > 0 else "")
        if hw.gpu_name
        else "không có GPU rời"
    )
    if hw.tier == "cloud-first":
        rec_vi = (
            "Khuyến nghị #1: Đăng nhập Google Antigravity CLI (dùng gói Google AI Ultra/Pro/Free có sẵn cho NidoOffice & NidoAgent) "
            "hoặc dán Google AI Studio GEMINI_API_KEY miễn phí ($0 — bật thêm NidoBox OCR). "
            "Không khuyến nghị chạy model 7B+ cục bộ trên máy dưới 8 GB RAM."
        )
    elif hw.tier == "hybrid":
        rec_vi = (
            "Máy chạy tốt cả Cloud miễn phí ($0) lẫn AI cục bộ Ollama gọn (khuyên dùng gemma3:4b cho cả Vision OCR + Tiếng Việt)."
        )
    else:
        rec_vi = (
            "Phần cứng mạnh — chạy mượt AI cục bộ 100% offline qua Ollama (gemma3:12b, qwen2.5vl:7b) hoặc kết hợp Cloud."
        )

    ol = local_info.get("ollama", {})
    if ol.get("state") == "ready":
        ol_txt = f"Ollama đang chạy ({len(ol.get('models', []))} model: {', '.join(ol.get('models', [])[:3])})"
    elif ol.get("state") == "installed-no-models":
        ol_txt = "Ollama đang chạy nhưng chưa tải model nào (0 model)"
    elif ol.get("state") == "installed-not-running":
        ol_txt = "Đã cài Ollama nhưng dịch vụ chưa chạy"
    else:
        ol_txt = "Chưa cài Ollama"

    return {
        "hardware": asdict(hw),
        "hardwareLineVi": (
            f"{hw.ram_gb:.1f} GB RAM · {hw.cpu_cores} CPU · {gpu_part} · {hw.free_disk_gb:.1f} GB trống ({hw.arch})"
        ),
        "recommendationVi": rec_vi,
        "ollamaStatusVi": ol_txt,
        "local": local_info,
        "capabilities": caps,
        "recommendedModels": [asdict(m) for m in rec_models],
        "anyAiReady": any(c["ready"] for c in caps.values()),
    }


def parse_ollama_sha256sum(sha_text: str, asset_name: str) -> str | None:
    for line in sha_text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            digest = parts[0].strip().lower()
            fname = parts[-1].lstrip("*./").strip()
            if fname == asset_name and re.fullmatch(r"[0-9a-f]{64}", digest):
                return digest
    return None


def install_ollama_user(
    home_dir: Path | None = None,
    on_progress: ProgressCallback | None = None,
    arch: str | None = None,
) -> tuple[bool, str, str | None]:
    """Install official Ollama release into ~/.local/share/nido/ollama with SHA256 verification (zero root)."""
    home = home_dir or Path.home()
    existing = resolve_ollama_binary(home)
    if existing:
        _ensure_ollama_user_service(home, existing)
        return True, f"Ollama đã có sẵn tại {existing}.", existing

    target_arch = normalize_linux_arch(arch)
    if target_arch not in ("amd64", "arm64"):
        return False, f"Kiến trúc chưa hỗ trợ cài Ollama tự động: {target_arch}", None

    if on_progress:
        on_progress(5, "Đang kiểm tra phiên bản Ollama chính chủ mới nhất trên GitHub...")

    st, rel_data, err = _http_json(OLLAMA_RELEASES_API, None, 12.0)
    if st != 200 or not isinstance(rel_data, dict):
        return False, f"Không truy vấn được bản phát hành Ollama: {err[:160]}", None

    assets = rel_data.get("assets", [])
    wanted_names = (f"ollama-linux-{target_arch}.tar.zst", f"ollama-linux-{target_arch}.tgz")
    asset_url = ""
    asset_name = ""
    sha_url = ""
    for a in assets:
        if not isinstance(a, dict):
            continue
        nm = str(a.get("name", ""))
        if nm == "sha256sum.txt":
            sha_url = str(a.get("browser_download_url", ""))
        if not asset_url and nm in wanted_names:
            asset_name = nm
            asset_url = str(a.get("browser_download_url", ""))

    if not asset_url or not sha_url:
        return False, f"Không tìm thấy gói Ollama ({target_arch}) hoặc sha256sum.txt trên GitHub Releases.", None

    if asset_name.endswith(".tar.zst") and not shutil.which("zstd"):
        return (
            False,
            "Cần công cụ giải nén `zstd` để cài Ollama (.tar.zst). Hãy cài gói `zstd` trước.",
            None,
        )

    if on_progress:
        on_progress(12, f"Đang tải bảng kiểm SHA256 cho {asset_name}...")
    try:
        with urllib.request.urlopen(
            urllib.request.Request(sha_url, headers={"User-Agent": "NidoHub-AI/1.0"}), timeout=15.0
        ) as r_sha:
            sha_text = r_sha.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return False, f"Không tải được sha256sum.txt của Ollama: {exc}", None

    expected_sha = parse_ollama_sha256sum(sha_text, asset_name)
    if not expected_sha:
        return False, f"Không tìm thấy mã SHA256 cho {asset_name} trong sha256sum.txt.", None

    install_dir = home / ".local" / "share" / "nido" / "ollama"
    install_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nido-ollama-") as td:
        arc_path = Path(td) / asset_name
        hasher = hashlib.sha256()
        if on_progress:
            on_progress(18, f"Đang tải {asset_name} từ Ollama chính chủ...")
        try:
            with urllib.request.urlopen(
                urllib.request.Request(asset_url, headers={"User-Agent": "NidoHub-AI/1.0"}),
                timeout=300.0,
            ) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with arc_path.open("wb") as fh:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        hasher.update(chunk)
                        done += len(chunk)
                        if on_progress and total > 0:
                            pct = 18 + int((done / total) * 62)
                            on_progress(
                                min(80, pct),
                                f"Đang tải Ollama ({done // (1024*1024)}/{total // (1024*1024)} MB)...",
                            )
        except Exception as exc:
            return False, f"Lỗi tải gói Ollama: {exc}", None

        actual_sha = hasher.hexdigest().lower()
        if actual_sha != expected_sha:
            return (
                False,
                f"Xác thực SHA256 Ollama thất bại (kỳ vọng {expected_sha[:12]}..., thực tế {actual_sha[:12]}...).",
                None,
            )

        if on_progress:
            on_progress(85, "Xác thực SHA256 thành công. Đang giải nén vào thư mục người dùng...")
        tar_cmd = ["tar", "--zstd", "-xf", str(arc_path), "-C", str(install_dir)] if asset_name.endswith(".zst") else [
            "tar",
            "-xzf",
            str(arc_path),
            "-C",
            str(install_dir),
        ]
        res_tar = subprocess.run(tar_cmd, capture_output=True, text=True, check=False)
        if res_tar.returncode != 0:
            return False, f"Lỗi giải nén Ollama: {(res_tar.stderr or res_tar.stdout).strip()[:200]}", None

    ol_bin = install_dir / "bin" / "ollama"
    if not ol_bin.is_file():
        return False, f"Không tìm thấy tệp thực thi ollama sau khi giải nén tại {ol_bin}.", None
    os.chmod(ol_bin, 0o755)

    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    link_path = local_bin / "ollama"
    try:
        if link_path.is_symlink() or link_path.exists():
            link_path.unlink()
        link_path.symlink_to(ol_bin)
    except OSError:
        pass

    if on_progress:
        on_progress(95, "Đang bật dịch vụ Ollama cục bộ (127.0.0.1:11434)...")
    _ensure_ollama_user_service(home, str(ol_bin))
    if on_progress:
        on_progress(100, "Đã cài đặt & khởi chạy Ollama thành công!")
    return True, f"Đã cài đặt Ollama chính chủ (SHA256 hợp lệ) tại {ol_bin}.", str(ol_bin)


def _ensure_ollama_user_service(home_dir: Path, ollama_bin: str) -> None:
    unit_dir = home_dir / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_file = unit_dir / "ollama.service"
    unit_file.write_text(
        "[Unit]\n"
        "Description=Ollama Local LLM Runtime (Ubuntu-Nido User Service)\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        "Environment=OLLAMA_HOST=127.0.0.1:11434\n"
        f"ExecStart={ollama_bin} serve\n"
        "Restart=on-failure\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )
    if shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "ollama.service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def pull_ollama_model(
    model_name: str,
    home_dir: Path | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[bool, str]:
    clean_model = model_name.strip()
    if not clean_model or not re.fullmatch(r"[a-zA-Z0-9._:/-]{2,96}", clean_model):
        return False, f"Tên model Ollama không hợp lệ: {model_name!r}"

    ol_bin = resolve_ollama_binary(home_dir)
    if not ol_bin:
        return False, "Chưa cài đặt Ollama trên máy. Hãy bấm 'Cài Ollama' trước."

    _ensure_ollama_user_service(home_dir or Path.home(), ol_bin)

    if on_progress:
        on_progress(5, f"Đang tải model {clean_model} về máy (Ollama)...")
    env = os.environ.copy()
    env.setdefault("OLLAMA_HOST", "127.0.0.1:11434")
    proc = subprocess.Popen(
        [ol_bin, "pull", clean_model],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    assert proc.stdout is not None
    pct_re = re.compile(r"(\d{1,3})%")
    last_lines: list[str] = []
    for raw in proc.stdout:
        line = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw).strip()
        if not line:
            continue
        last_lines.append(line)
        m = pct_re.search(line)
        pct = int(m.group(1)) if m else 35
        if on_progress:
            on_progress(min(98, max(5, pct)), f"{clean_model}: {line[:120]}")

    rc = proc.wait()
    if rc == 0:
        if on_progress:
            on_progress(100, f"Đã tải xong model {clean_model}!")
        return True, f"Đã tải thành công model cục bộ {clean_model}."
    tail = " | ".join(last_lines[-2:]) if last_lines else f"mã lỗi {rc}"
    return False, f"Tải model {clean_model} thất bại: {tail[:200]}"
