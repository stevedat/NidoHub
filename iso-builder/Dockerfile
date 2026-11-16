FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    CI=1 \
    APT_LOCK_TIMEOUT=600

RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        apt-transport-https \
        ca-certificates \
        curl \
        dpkg-dev \
        file \
        fontconfig \
        git \
        gnupg \
        locales \
        make \
        p7zip-full \
        python3 \
        rsync \
        squashfs-tools \
        sudo \
        unzip \
        wget \
        xorriso && \
    if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
        apt-get install -y --no-install-recommends isolinux syslinux syslinux-common syslinux-utils grub-pc-bin grub-efi-amd64-bin; \
    else \
        apt-get install -y --no-install-recommends grub-efi-arm64-bin; \
    fi && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/iso-builder

CMD ["/bin/bash"]
