# Helm Deployer

A lightweight Alpine-based Docker image for Kubernetes deployments and CI/CD pipelines.

The image includes a curated set of DevOps tools commonly used for GitLab CI, GitHub Actions, Jenkins, and other automation systems. All major components are versioned through environment variables, making updates simple and allowing automated version checks.

## Included tools

- kubectl (latest stable)
- Helm
- Helm plugins:
    - helm-secrets
    - helm-push
- kubedog
- crane / gcrane / krane
- sops
- Yandex Cloud CLI (yc)
- rclone
- Docker CLI
- git
- jq
- yq
- rsync
- curl
- bash
- Python 3 + pip

## Features

- Based on Alpine Linux
- Multi-architecture support (amd64 / arm64)
- Configurable component versions via `ENV`
- Suitable for Kubernetes deployments and Helm-based release pipelines
- Supports encrypted Helm charts with SOPS and helm-secrets
- Includes kubedog for deployment status monitoring
- Regularly updated with the latest stable component versions

## Typical use cases

- GitLab CI/CD
- GitHub Actions
- Jenkins
- Kubernetes deployments
- Helm releases
- Infrastructure automation

## Image

```bash
docker pull sanburst/helm-deployer:latest