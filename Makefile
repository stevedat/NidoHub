.PHONY: help assets packages ime-bridge apt-repo dev-apt dev-apt-deploy release-apt release-apt-deploy release-apt-native release-apt-native-deploy check security-audit native-packages native-packages-amd64 native-packages-arm64 iso iso-arm64 iso-arm64-fast docker-iso clean

UBUNTU_VERSION     ?= 26.04
NIDO_VERSION       ?= 0.2.0
DEB_VERSION_SUFFIX ?= +dev$(shell date -u +%Y%m%d%H%M)
APT_GPG_KEY        ?= AC980631CB8BA9D2602A0990528AF2083F61DC2F
APT_PAGES_PROJECT  ?= ubuntu-nido-apt

help:
	@echo "NidoOS (NidoHub) Build & Verification Targets"
	@echo ""
	@echo "  make check              Run full static analysis, packaging, APT repo, and runtime smoke tests"
	@echo "  make assets             Generate NidoOS branding PNG assets (watermark, GRUB splash, wallpaper, logo)"
	@echo "  make packages           Build NidoOS .deb packages into packages/dist/"
	@echo "  make ime-bridge         Build amd64/arm64 nido-ime-bridge (zero-underline Sen Viet) .debs into packages/vendor/"
	@echo "  make apt-repo           Build static APT repository into apt-repo/"
	@echo "  make dev-apt            Build & GPG-sign dev packages (+dev...) in apt-repo/ for fast UTM apt upgrade"
	@echo "  make dev-apt-deploy     Build, sign, and deploy dev apt-repo/ to $(APT_PAGES_PROJECT) via wrangler"
	@echo "  make release-apt        Build & GPG-sign clean release packages (no +dev suffix) in apt-repo/"
	@echo "  make release-apt-deploy Build, sign, and deploy clean release apt-repo/ to $(APT_PAGES_PROJECT)"
	@echo "  make security-audit     Run integrity verification, firewall & distribution hardening self-tests"
	@echo "  make native-packages        Build Stage 2 native Cython/SEA packages for both amd64 & arm64 (Docker)"
	@echo "  make native-packages-amd64  Build Stage 2 native Cython/SEA packages for amd64 (requires Docker)"
	@echo "  make native-packages-arm64  Build Stage 2 native Cython/SEA packages for arm64 (requires Docker)"
	@echo "  make release-apt-native-deploy  Build Stage 1+2 native APT repo (resolute=native, noble=all) & deploy"
	@echo "  make iso            Build NidoOS amd64 ISO (Linux host/root)"
	@echo "  make iso-arm64      Build NidoOS arm64 ISO (Linux host/root)"
	@echo "  make iso-arm64-fast Build NidoOS arm64 ISO locally (FAST_ISO=1 + ext4 workspace/cache)"
	@echo "  make docker-iso     Build ISO inside Docker container (macOS/Linux)"
	@echo "  make clean          Remove generated artifacts (dist, apt-repo, ISO build dirs)"

assets:
	python3 branding/generate-assets.py

packages: assets
	bash packages/build-all.sh

ime-bridge:
	APT_GPG_KEY="$(APT_GPG_KEY)" bash scripts/build_ime_bridge.sh --arch all --out-dir packages/vendor

apt-repo: packages
	python3 scripts/build_apt_repo.py --dist-dir packages/dist --vendor-dir packages/vendor --out-dir apt-repo

dev-apt:
	@test -n "$(DEB_VERSION_SUFFIX)" || { echo "DEB_VERSION_SUFFIX must be non-empty"; exit 1; }
	DEB_VERSION_SUFFIX="$(DEB_VERSION_SUFFIX)" bash packages/build-all.sh
	python3 scripts/build_apt_repo.py --dist-dir packages/dist --vendor-dir packages/vendor --out-dir apt-repo
	cp packages/nido-desktop-settings/files/etc/apt/keyrings/ubuntu-nido.asc apt-repo/ubuntu-nido.asc
	@if command -v gpg >/dev/null 2>&1 && gpg --list-secret-keys "$(APT_GPG_KEY)" >/dev/null 2>&1; then \
		for suite in resolute noble; do \
			rel="apt-repo/dists/$${suite}/Release"; \
			gpg --batch --yes -u "$(APT_GPG_KEY)" -abs -o "apt-repo/dists/$${suite}/Release.gpg" "$$rel"; \
			gpg --batch --yes -u "$(APT_GPG_KEY)" --clearsign -o "apt-repo/dists/$${suite}/InRelease" "$$rel"; \
		done; \
		echo "Signed apt-repo Release metadata with $(APT_GPG_KEY)."; \
	else \
		echo "WARN: GPG secret key $(APT_GPG_KEY) not found; apt-repo left unsigned." >&2; \
	fi

dev-apt-deploy: dev-apt
	wrangler pages deploy apt-repo --project-name="$(APT_PAGES_PROJECT)" --branch=main --commit-dirty=true

release-apt:
	DEB_VERSION_SUFFIX="" bash packages/build-all.sh
	python3 scripts/build_apt_repo.py --dist-dir packages/dist --vendor-dir packages/vendor --out-dir apt-repo
	cp packages/nido-desktop-settings/files/etc/apt/keyrings/ubuntu-nido.asc apt-repo/ubuntu-nido.asc
	@if command -v gpg >/dev/null 2>&1 && gpg --list-secret-keys "$(APT_GPG_KEY)" >/dev/null 2>&1; then \
		for suite in resolute noble; do \
			rel="apt-repo/dists/$${suite}/Release"; \
			gpg --batch --yes -u "$(APT_GPG_KEY)" -abs -o "apt-repo/dists/$${suite}/Release.gpg" "$$rel"; \
			gpg --batch --yes -u "$(APT_GPG_KEY)" --clearsign -o "apt-repo/dists/$${suite}/InRelease" "$$rel"; \
		done; \
		echo "Signed clean release apt-repo Release metadata with $(APT_GPG_KEY)."; \
	else \
		echo "WARN: GPG secret key $(APT_GPG_KEY) not found; apt-repo left unsigned." >&2; \
	fi

release-apt-deploy: release-apt
	wrangler pages deploy apt-repo --project-name="$(APT_PAGES_PROJECT)" --branch=main --commit-dirty=true

check:
	bash tests/verify_repo.sh

security-audit:
	python3 packages/nido-desktop-settings/files/usr/bin/nido-security-verify --self-test
	bash packages/nido-desktop-settings/files/usr/libexec/ubuntu-nido/configure-firewall --self-test
	python3 scripts/harden_dist.py --self-test
	node scripts/harden_js.mjs --self-test
	bash scripts/native-build/build_native_debs.sh --dry-run

native-packages:
	APT_GPG_KEY="$(APT_GPG_KEY)" bash scripts/native-build/build_native_debs.sh --arch all

native-packages-amd64:
	APT_GPG_KEY="$(APT_GPG_KEY)" bash scripts/native-build/build_native_debs.sh --arch amd64

native-packages-arm64:
	APT_GPG_KEY="$(APT_GPG_KEY)" bash scripts/native-build/build_native_debs.sh --arch arm64

release-apt-native:
	DEB_VERSION_SUFFIX="" bash packages/build-all.sh
	@if [ ! -f packages/dist/native/nidoai-gateway_0.2.0_arm64.deb ] || [ ! -f packages/dist/native/nidoai-gateway_0.2.0_amd64.deb ]; then \
		APT_GPG_KEY="$(APT_GPG_KEY)" DEB_VERSION_SUFFIX="" bash scripts/native-build/build_native_debs.sh --arch all; \
	fi
	python3 scripts/build_apt_repo.py --dist-dir packages/dist --vendor-dir packages/vendor --native-dir packages/dist/native --out-dir apt-repo
	cp packages/nido-desktop-settings/files/etc/apt/keyrings/ubuntu-nido.asc apt-repo/ubuntu-nido.asc
	@if command -v gpg >/dev/null 2>&1 && gpg --list-secret-keys "$(APT_GPG_KEY)" >/dev/null 2>&1; then \
		for suite in resolute noble; do \
			rel="apt-repo/dists/$${suite}/Release"; \
			gpg --batch --yes -u "$(APT_GPG_KEY)" -abs -o "apt-repo/dists/$${suite}/Release.gpg" "$$rel"; \
			gpg --batch --yes -u "$(APT_GPG_KEY)" --clearsign -o "apt-repo/dists/$${suite}/InRelease" "$$rel"; \
		done; \
		echo "Signed Stage 2 native release apt-repo Release metadata with $(APT_GPG_KEY)."; \
	else \
		echo "WARN: GPG secret key $(APT_GPG_KEY) not found; apt-repo left unsigned." >&2; \
	fi

release-apt-native-deploy: release-apt-native
	wrangler pages deploy apt-repo --project-name="$(APT_PAGES_PROJECT)" --branch=main --commit-dirty=true

iso: assets packages
	cd iso-builder && sudo TARGET_ARCH=amd64 UBUNTU_VERSION=$(UBUNTU_VERSION) NIDO_VERSION=$(NIDO_VERSION) ./build.sh

iso-arm64: assets packages
	cd iso-builder && sudo TARGET_ARCH=arm64 UBUNTU_VERSION=$(UBUNTU_VERSION) NIDO_VERSION=$(NIDO_VERSION) ./build.sh

iso-arm64-fast:
	UBUNTU_VERSION=$(UBUNTU_VERSION) NIDO_VERSION=$(NIDO_VERSION) bash iso-builder/scripts/local-arm64.sh --fast

docker-iso: assets packages
	cd iso-builder && docker compose run --rm builder ./build.sh

clean:
	rm -rf packages/dist packages/*/stage apt-repo
	rm -f iso-builder/config/includes.chroot/usr/share/backgrounds/ubuntu-nido/*.png
	rm -rf iso-builder/build iso-builder/Ubuntu-Nido-*.iso iso-builder/NidoOS-*.iso
