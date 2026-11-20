.PHONY: help assets packages apt-repo dev-apt dev-apt-deploy release-apt release-apt-deploy check iso iso-arm64 iso-arm64-fast docker-iso clean

UBUNTU_VERSION     ?= 26.04
NIDO_VERSION       ?= 0.2.0
DEB_VERSION_SUFFIX ?= +dev$(shell date -u +%Y%m%d%H%M)
APT_GPG_KEY        ?= AC980631CB8BA9D2602A0990528AF2083F61DC2F
APT_PAGES_PROJECT  ?= ubuntu-nido-apt

help:
	@echo "Ubuntu-Nido (NidoHub) Build & Verification Targets"
	@echo ""
	@echo "  make check              Run full static analysis, packaging, APT repo, and runtime smoke tests"
	@echo "  make assets             Generate branding PNG assets (watermark, GRUB splash, wallpaper)"
	@echo "  make packages           Build Ubuntu-Nido .deb packages into packages/dist/"
	@echo "  make apt-repo           Build static APT repository into apt-repo/"
	@echo "  make dev-apt            Build & GPG-sign dev packages (+dev...) in apt-repo/ for fast UTM apt upgrade"
	@echo "  make dev-apt-deploy     Build, sign, and deploy dev apt-repo/ to $(APT_PAGES_PROJECT) via wrangler"
	@echo "  make release-apt        Build & GPG-sign clean release packages (no +dev suffix) in apt-repo/"
	@echo "  make release-apt-deploy Build, sign, and deploy clean release apt-repo/ to $(APT_PAGES_PROJECT)"
	@echo "  make iso            Build Ubuntu-Nido amd64 ISO (Linux host/root)"
	@echo "  make iso-arm64      Build Ubuntu-Nido arm64 ISO (Linux host/root)"
	@echo "  make iso-arm64-fast Build Ubuntu-Nido arm64 ISO locally (FAST_ISO=1 + ext4 workspace/cache)"
	@echo "  make docker-iso     Build ISO inside Docker container (macOS/Linux)"
	@echo "  make clean          Remove generated artifacts (dist, apt-repo, ISO build dirs)"

assets:
	python3 branding/generate-assets.py

packages: assets
	bash packages/build-all.sh

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
	rm -rf iso-builder/build iso-builder/Ubuntu-Nido-*.iso
