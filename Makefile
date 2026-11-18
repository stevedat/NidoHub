.PHONY: help assets packages apt-repo check iso iso-arm64 iso-arm64-fast docker-iso clean

UBUNTU_VERSION ?= 26.04
NIDO_VERSION   ?= 0.1.0

help:
	@echo "Ubuntu-Nido (NidoHub) Build & Verification Targets"
	@echo ""
	@echo "  make check          Run full static analysis, packaging, APT repo, and runtime smoke tests"
	@echo "  make assets         Generate branding PNG assets (watermark, GRUB splash, wallpaper)"
	@echo "  make packages       Build Ubuntu-Nido .deb packages into packages/dist/"
	@echo "  make apt-repo       Build static APT repository into apt-repo/"
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
	python3 scripts/build_apt_repo.py --dist-dir packages/dist --out-dir apt-repo

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
