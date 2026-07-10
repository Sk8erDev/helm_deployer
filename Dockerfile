FROM alpine:3.23.5
ARG TARGETARCH
ENV KUBECTL_DIR=/usr/local/bin
# github: helm/helm (keep-major)
ENV HELM_VERSION=3.21.3
ENV HELM_HOME=/helm/
# github: google/go-containerregistry
ENV CR_VERSION=0.21.7
# github: getsops/sops
ENV SOPS_VERSION=3.13.2
# github: werf/kubedog
ENV KUBEDOG_VERSION=0.13.0
ENV PATH=$PATH:$HELM_HOME
ENV YC_HOME=/yc
ENV GPG_KEY_DIR=/root/.gnupg
ENV PATH $HELM_HOME:$YC_HOME/bin:$PATH
ENV TERM=xterm-256color
ENV KUBEDOG_FORCE_COLOR=true

RUN env

# Install dependencies
RUN apk --no-cache add \
        curl \
        python3 \
        py3-pip \
        py-crcmod \
        bash \
        libc6-compat \
        openssh-client \
        git \
        gnupg \
        jq \
        yq \
        rsync \
		docker \
        tar \
        ca-certificates \
        git \
        util-linux \
        coreutils

# Download and install the latest stable kubectl binary
RUN ARCH="${TARGETARCH:-amd64}" && \
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/${ARCH}/kubectl" && \
    chmod +x kubectl && \
    mv kubectl ${KUBECTL_DIR}/kubectl && \
    kubectl version --client

# Installing helm
RUN ARCH="${TARGETARCH:-amd64}" && \
    curl -LO "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${ARCH}.tar.gz" && \
    tar xzf helm-v${HELM_VERSION}-linux-${ARCH}.tar.gz && \
    mv linux-${ARCH} $HELM_HOME && \
    rm helm-v${HELM_VERSION}-linux-${ARCH}.tar.gz && \
    mkdir -p $HELM_HOME/plugins && \
	helm plugin install https://github.com/jkroepke/helm-secrets && \
    helm plugin install https://github.com/chartmuseum/helm-push && \
    helm version --short --client

# Installing container registry tools
RUN ARCH="${TARGETARCH:-amd64}" && \
    if [ "$ARCH" = "amd64" ]; then CR_ARCH="x86_64"; else CR_ARCH="$ARCH"; fi && \
    curl -LO "https://github.com/google/go-containerregistry/releases/download/v${CR_VERSION}/go-containerregistry_Linux_${CR_ARCH}.tar.gz" && \
    tar zxvf go-containerregistry_Linux_${CR_ARCH}.tar.gz && \
    chmod +x crane gcrane krane && \
    mv crane gcrane krane /usr/local/bin && \
    rm go-containerregistry_Linux_${CR_ARCH}.tar.gz

# Installing sops
RUN ARCH="${TARGETARCH:-amd64}" && \
    set -xe && curl -LO "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.${ARCH}" && \
    mv sops-v${SOPS_VERSION}.linux.${ARCH} /usr/local/bin/sops && \
	chmod +x /usr/local/bin/sops

# Setup GPG trust key registry
RUN mkdir -p $GPG_KEY_DIR && chmod 700 $GPG_KEY_DIR

# Verify installations
RUN helm plugin list && gpg --list-keys

# Install yandex cli
RUN curl https://storage.yandexcloud.net/yandexcloud-yc/install.sh | \
    bash -s -- -i ${YC_HOME} -n


# Installing kubedog
RUN ARCH="${TARGETARCH:-amd64}" && \
    KUBEDOG_ARCH="linux-${ARCH}" && \
    curl -LO "https://tuf.kubedog.werf.io/targets/releases/${KUBEDOG_VERSION}/${KUBEDOG_ARCH}/bin/kubedog" && \
    curl -LO "https://tuf.kubedog.werf.io/targets/signatures/${KUBEDOG_VERSION}/${KUBEDOG_ARCH}/bin/kubedog.sig" && \
    curl -sSL https://werf.io/kubedog.asc | gpg --import && \
    gpg --verify kubedog.sig kubedog && \
    chmod +x kubedog && \
    mv kubedog /usr/local/bin/kubedog && \
    rm kubedog.sig


# Install rclone
RUN ARCH="${TARGETARCH:-amd64}" && \
    curl -O "https://downloads.rclone.org/rclone-current-linux-${ARCH}.zip" && \
    unzip rclone-current-linux-${ARCH}.zip && \
    cd rclone-*-linux-${ARCH} && \
    cp rclone /usr/bin/ && \
    chown root:root /usr/bin/rclone && \
    chmod 755 /usr/bin/rclone

VOLUME ["/root/.config"]