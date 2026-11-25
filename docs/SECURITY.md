# NidoOS Security & Anti-Reverse-Engineering Architecture

NidoOS v0.2.0 implements a 4-layer defense-in-depth architecture to protect user credentials, NidoHub, NidoAgent, NidoAI Gateway, and NidoOffice against reverse engineering, tampering, privilege escalation, and local/browser-based attacks.

---

## Layer 1 — Privilege Boundaries & Launcher Integrity

- **Polkit Admin Prompting**:
  - `com.nidoholdings.nidohub.pkg` (`/usr/libexec/nido-hub/nido-pkg-helper`) and `com.nidoholdings.nidooffice.update` (`/usr/lib/nidooffice-desktop/install-runtime.sh`) enforce `auth_admin` (requiring explicit admin authentication without indefinite caching).
- **Root Helper Runtime Lockdown (`nido-pkg-helper`)**:
  - When invoked as `uid == 0`, `enforce_root_security_guards()` forbids runtime overrides of `--vendors-file`, `--key-file`, and `--root`, ignores `NIDO_PKG_ARCH` / `NIDO_UBUNTU_CODENAME` environment overrides, verifies `/usr/share/nido-hub/vendors.json` is root-owned and non-writable by non-root users, and blocks replacing core system packages via unsigned local `.deb` installs.
- **Anti-Hijacking System Launchers**:
  - All `/usr/bin/*` entrypoints (`nido-hub`, `nidoai`, `nidoagent`, `nidooffice`, `nidooffice-desktop`) detect when invoked from `/usr/bin/*` and lock execution paths to root-owned `/usr/lib/*` and `/opt/*` targets, ignoring `NIDO*_ENTRY` / `NIDO_SKILLS_DIR` overrides and refusing user-writable `~/.local/share/` binary precedence.

---

## Layer 2 — NidoAI Gateway, Account & Agent Hardening

- **Anti-DNS-Rebinding & Origin Isolation**:
  - Gateway checks every HTTP request's `Host` and `Origin` headers against strict loopback patterns (`127.0.0.1`, `localhost`, `[::1]`), rejecting cross-origin or rebinding attempts with `403`.
- **Default-Disabled Web UI & Token Exposure Prevention**:
  - `GET /` (Control Panel) is **disabled by default** (`404`, no `Set-Cookie`) unless `NIDOAI_ENABLE_WEB_UI=1` is explicitly enabled, in which case strict per-boot CSP (`nonce-...`, `frame-ancestors 'none'`) and security headers (`Referrer-Policy: no-referrer`, `Cross-Origin-Resource-Policy: same-origin`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Cache-Control: no-store`) apply.
  - `GET /api/admin/pairing-token` is permanently disabled (`403`).
  - Unauthenticated `GET /healthz` omits PII (`email`).
- **Ephemeral Per-Boot Tokens & Rate Limiting**:
  - `nidoai serve` generates a fresh ephemeral bearer token on each service start unless `NIDOAI_CLIENT_TOKEN` is explicitly pinned, preserving the active token across hot-reloads of `.env.local` and optionally listening on a `0600` Unix Domain Socket (`$XDG_RUNTIME_DIR/nidoai-gateway.sock`).
  - Sliding-window rate limiters return `429` (`Retry-After: 60`) after 20 failed auth requests/min or 10 login requests/min.
- **Codex Workspace Path Sandbox**:
  - `/api/admin/codex/*` routes validate `cwd` via `realpath` within `$HOME` or `$TMPDIR`, denying system paths (`/etc`, `/root`, `/proc`, `/var`) and credential directories (`~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.kube`, `~/.nidoai`, `~/.config/ubuntu-nido`).
- **Account Provider Endpoint Pinning**:
  - Both `nido_account.py` and `nido-account-adapter.js` ignore user-writable `~/.config/ubuntu-nido/account-provider.json`, read `/etc/ubuntu-nido/account-provider.json` only if root-owned (`0644`/`0640`), and pin HTTPS requests to verified Nido/IdP domains (unless `NIDO_ALLOW_CUSTOM_PROVIDER=1` is set).
- **NidoHub Process Anti-Dump & Keyring Parity**:
  - `nido-hub` calls `prctl(PR_SET_DUMPABLE, 0)` on Linux to block non-root memory inspection, and shares `/run/user/$UID/bus` discovery with `secret-store.js` for consistent GNOME Keyring (`libsecret`) storage.
- **NidoAgent CLI Isolation**:
  - Validates `runtime.json` `baseUrl` is loopback-only, strips gateway tokens and API keys from child Agent CLI (`agy`, `codex`, `claude`) environments, blocks attaching sensitive files (`~/.ssh/*`, `~/.gnupg/*`, `~/.nidoai/*`, `~/.config/ubuntu-nido/*`, `/etc/shadow`, `/proc/*/environ`), and wraps attached file payloads in `<untrusted_attachment>` delimiters.

---

## Layer 3 — OS Kernel, AppArmor, Firewall & Integrity Verification

- **Kernel Hardening (`/etc/sysctl.d/90-nidoos-hardening.conf`)**:
  - `kernel.yama.ptrace_scope = 2`, `fs.suid_dumpable = 0`, `kernel.kptr_restrict = 2`, `kernel.dmesg_restrict = 1`, `kernel.unprivileged_bpf_disabled = 1`, protected symlinks/hardlinks/FIFOs/regular files, and IP spoofing/redirect protections.
- **Systemd & AppArmor Confinement**:
  - `nidoai-gateway.service` executes `/usr/libexec/nidoai/gateway-daemon` with `NoNewPrivileges=yes`, `PrivateTmp=yes`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, `LimitCORE=0`, `UMask=0077`, and memory/CPU bounds.
  - Enforcing AppArmor profiles protect `/usr/libexec/nidoai/gateway-daemon` (`etc/apparmor.d/nidoai-gateway`), `/usr/libexec/nido-hub/nido-pkg-helper` (`etc/apparmor.d/nido-pkg-helper`), and NidoOffice (`/etc/apparmor.d/nidooffice`).
- **UFW Firewall (`/usr/libexec/ubuntu-nido/configure-firewall`)**:
  - Default `deny incoming`, `allow outgoing`, allowing only multicast mDNS (`5353/udp`) and NidoDrop LAN (`54321/tcp`) scoped strictly to RFC1918 and link-local IPv6 subnets.
- **Signed Package Integrity Manifests & Auditing**:
  - Every NidoOS `.deb` includes `/usr/share/nido-security/manifests/<pkg>.sha256` (plus detached GPG signature `.asc` signed by key `AC980631CB8BA9D2602A0990528AF2083F61DC2F`).
  - `nido-security-verify` and `nido-doctor` verify file hashes, permissions, root ownership, GPG signatures, sysctl values, AppArmor status, and keyring usage.

---

## Layer 4 — Anti-Reverse-Engineering Distribution Pipeline

1. **Stage 1 (Default `Architecture: all` Distribution)**:
   - Triggered automatically by `packages/build-all.sh` (`NIDO_HARDEN_DIST=1`) without altering readable source files in Git.
   - `scripts/harden_dist.py` strips docstrings/comments via Python `ast` and wraps staged `.py` payloads with `zlib` + deterministic XOR + `base85` (compiled with `-OO` bytecode in `postinst`).
   - `scripts/harden_js.mjs` lexically strips comments/whitespace and validates output via `node --check`.
2. **Stage 2 (Native `amd64` + `arm64` Commercial Builds)**:
   - Triggered via `make native-packages-amd64` / `make native-packages-arm64` (`scripts/native-build/build_native_debs.sh`) using `Dockerfile.native`.
   - `compile_hub_cython.py` compiles core NidoHub Python modules to stripped ELF `.so` extensions (`-O2 -fvisibility=hidden`).
   - `build_gateway_sea.sh` bundles and compiles `nidoai-gateway` and `nidoagent-cli` into Node 22+ Single Executable Application (SEA) binaries.
   - Commercial proprietary IP and server-side quota/licensing logic live on private backend services (`ai-proxy` + RLS) and private release repositories rather than client-side trust checks.

### NidoOffice Desktop Electron Fuses Checklist (Upstream Build)
Before shipping commercial NidoOffice Desktop binaries (`v0.13.x+`), verify these Electron Fuses are flipped in the Electron packaging pipeline:
- `EnableRunAsNode = false`
- `EnableNodeOptionsEnvironmentVariable = false`
- `EnableNodeCliInspectArguments = false`
- `EnableEmbeddedAsarIntegrityValidation = true`
- `OnlyLoadAppFromAsar = true`
