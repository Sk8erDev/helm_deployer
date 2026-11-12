#!/usr/bin/env python3
import re
import urllib.request
import urllib.error
import json
import os
import sys
import subprocess
import html
import shutil

# --- Настройки ---
DOCKERFILE_PATH = "Dockerfile"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")  # Опционально, для обхода лимитов GitHub API
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DOCKER_USR = os.environ.get("DOCKER_USR")
DOCKER_PWD = os.environ.get("DOCKER_PWD")

# --- Сопоставление по умолчанию (если нет аннотации в Dockerfile) ---
ENV_REPO_MAP = {
    "SOPS": "getsops/sops",
    "HELM": "helm/helm",
    "CR": "google/go-containerregistry",
    "KUBEDOG": "werf/kubedog",
    "WERF": "werf/werf"
}

def send_telegram_notification(message):
    """Отправить уведомление в Telegram-канал."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Пропуск отправки Telegram: TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print("✅ Уведомление в Telegram отправлено.")
            else:
                print(f"❌ Ошибка отправки Telegram: статус {response.status}")
    except Exception as e:
        print(f"❌ Ошибка отправки Telegram: {e}")

def semver_key(v):
    """Функция для семантического сравнения версий по числам."""
    parts = re.findall(r"\d+", v)
    return tuple(map(int, parts)) if parts else (0,)

def get_latest_alpine_version():
    """Получить последнюю стабильную версию Alpine (не latest)."""
    url = "https://registry.hub.docker.com/v2/repositories/library/alpine/tags?page_size=100&ordering=last_updated"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                tags = data.get("results", [])
                for tag in tags:
                    tag_name = tag.get("name", "")
                    if tag_name != "latest" and re.match(r"^\d+(\.\d+)*$", tag_name):
                        return tag_name
    except Exception as e:
        print(f"❌ Ошибка запроса Alpine: {e}")
    print("❌ Не удалось найти стабильную версию Alpine.")
    return None

def get_latest_github_version(repo, prefix="v", current_version=None, keep_mode=None, keep_major=False):
    """Получить последнюю стабильную версию с GitHub (семантически наибольшую)."""
    url = f"https://api.github.com/repos/{repo}/releases?per_page=100"
    headers = {"User-Agent": "Mozilla/5.0"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                releases = json.loads(response.read().decode())
                versions = []
                for release in releases:
                    tag_name = release.get("tag_name", "")
                    if prefix and tag_name.startswith(prefix):
                        version = tag_name[len(prefix):]
                    else:
                        version = tag_name
                    if re.search(r"(beta|rc|alpha)", version, re.IGNORECASE):
                        continue
                    if version:
                        versions.append(version)
                
                if versions:
                    versions.sort(key=semver_key, reverse=True)
                    mode = keep_mode or ("keep-major" if keep_major else None)

                    # Если нужно ограничиться текущей минорной версией (например, 1.2.x)
                    if mode == "keep-minor" and current_version:
                        target_minor = semver_key(current_version)[:2]
                        filtered = [v for v in versions if semver_key(v)[:2] == target_minor]
                        return filtered[0] if filtered else current_version

                    # Если нужно ограничиться текущей мажорной версией (например, 3.x)
                    if mode == "keep-major" and current_version:
                        target_major = semver_key(current_version)[0]
                        filtered = [v for v in versions if semver_key(v)[0] == target_major]
                        return filtered[0] if filtered else current_version
                            
                    return versions[0]
    except Exception as e:
        print(f"❌ Ошибка запроса GitHub для {repo}: {e}")
        return None
    print(f"❌ Не найден стабильный релиз для {repo}.")
    return None

def map_target_to_component(target):
    """Сопоставление цели сканирования Trivy с компонентом Dockerfile."""
    t = target.lower()
    if "alpine" in t:
        return "ALPINE"
    elif "helm" in t:
        return "HELM"
    elif "sops" in t:
        return "SOPS"
    elif "kubedog" in t:
        return "KUBEDOG"
    elif "werf" in t:
        return "WERF"
    elif any(k in t for k in ["crane", "gcrane", "krane", "containerregistry"]):
        return "CR"
    return None

def run_trivy_scan(image_name, sarif_output="trivy-results.sarif", json_output="trivy-results.json"):
    """
    Запустить сканирование уязвимостей через Trivy.
    Генерирует JSON для анализа и SARIF для загрузки в GitHub Security.
    """
    trivy_bin = shutil.which("trivy")
    print(f"\n🔍 Запуск SAST / Vulnerability сканирования образа {image_name}...")
    
    # 1. Генерация SARIF отчёта (для GitHub Code Scanning)
    sarif_cmd = []
    if trivy_bin:
        sarif_cmd = [
            trivy_bin, "image",
            "--scanners", "vuln",
            "--severity", "CRITICAL,HIGH",
            "--format", "sarif",
            "-o", sarif_output,
            image_name
        ]
    else:
        sarif_cmd = [
            "docker", "run", "--rm",
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-v", f"{os.getcwd()}:/work", "-w", "/work",
            "aquasec/trivy:latest", "image",
            "--scanners", "vuln",
            "--severity", "CRITICAL,HIGH",
            "--format", "sarif",
            "-o", sarif_output,
            image_name
        ]
    
    try:
        subprocess.run(sarif_cmd, capture_output=True, text=True, check=False)
        if os.path.exists(sarif_output):
            print(f"✅ SARIF отчет сохранен: {sarif_output}")
    except Exception as e:
        print(f"⚠️ Не удалось сгенерировать SARIF: {e}")

    # 2. Генерация JSON отчёта (для программного анализа уязвимостей)
    json_cmd = []
    if trivy_bin:
        json_cmd = [
            trivy_bin, "image",
            "--scanners", "vuln",
            "--severity", "CRITICAL,HIGH",
            "--format", "json",
            "-o", json_output,
            image_name
        ]
    else:
        json_cmd = [
            "docker", "run", "--rm",
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-v", f"{os.getcwd()}:/work", "-w", "/work",
            "aquasec/trivy:latest", "image",
            "--scanners", "vuln",
            "--severity", "CRITICAL,HIGH",
            "--format", "json",
            "-o", json_output,
            image_name
        ]
    
    try:
        subprocess.run(json_cmd, capture_output=True, text=True, check=False)
        if not os.path.exists(json_output):
            print("⚠️ Файл отчета Trivy JSON не создан.")
            return None
            
        with open(json_output, "r") as f:
            trivy_data = json.load(f)
            
        results = trivy_data.get("Results", [])
        vulns_by_comp = {}
        all_vulns = []
        total_critical = 0
        total_high = 0
        
        for r in results:
            target = r.get("Target", "")
            comp = map_target_to_component(target)
            for v in r.get("Vulnerabilities", []) or []:
                sev = v.get("Severity", "UNKNOWN")
                cve = v.get("VulnerabilityID", "")
                pkg = v.get("PkgName", "")
                inst_ver = v.get("InstalledVersion", "")
                fixed_ver = v.get("FixedVersion", "")
                
                if sev == "CRITICAL":
                    total_critical += 1
                elif sev == "HIGH":
                    total_high += 1
                    
                info = {
                    "cve": cve,
                    "severity": sev,
                    "pkg": pkg,
                    "installed": inst_ver,
                    "fixed": fixed_ver,
                    "target": target,
                    "component": comp
                }
                all_vulns.append(info)
                if comp:
                    vulns_by_comp.setdefault(comp, []).append(info)
                    
        return {
            "total_critical": total_critical,
            "total_high": total_high,
            "vulns_by_comp": vulns_by_comp,
            "all_vulns": all_vulns
        }
    except Exception as e:
        print(f"❌ Ошибка выполнения Trivy сканирования: {e}")
        return None

def check_and_update_dockerfile(dockerfile_path, only_components=None, force_relax_keep=False):
    """
    Проверить и обновить версии компонентов в Dockerfile.
    Если указан only_components, обновляет только их.
    Если force_relax_keep=True, разрешает поиск более новой мажорной/минорной версии.
    """
    with open(dockerfile_path, "r") as file:
        dockerfile_content = file.readlines()

    updated_content = []
    latest_alpine_version = None
    if only_components is None or "ALPINE" in only_components:
        latest_alpine_version = get_latest_alpine_version()

    changes = []
    for i, line in enumerate(dockerfile_content):
        original_line = line

        # --- Обновляем Alpine ---
        if line.startswith("FROM alpine:") and latest_alpine_version:
            if only_components is None or "ALPINE" in only_components:
                match_alpine = re.match(r"FROM alpine:([^ \r\n]+)", line)
                old_alpine_version = match_alpine.group(1) if match_alpine else None
                line = re.sub(r"FROM alpine:[^ \r\n]+", f"FROM alpine:{latest_alpine_version}", line)
                if line != original_line:
                    print(f"🔹 Alpine: {original_line.strip()} → {line.strip()}")
                    changes.append(f"Alpine: {old_alpine_version} → {latest_alpine_version}")

        # --- Обновляем ENV переменные ---
        match = re.match(r"^ENV (\w+)_VERSION=([\w.-]+)", line.strip())
        if match:
            env_var, current_version = match.groups()
            latest_version = None

            if only_components is None or env_var in only_components:
                prev_line = dockerfile_content[i - 1] if i > 0 else ""
                repo = None
                option = None
                if prev_line.strip().startswith("# github:"):
                    comment_match = re.match(r"^#\s*github:\s*([\w.-]+/[\w.-]+)(?:\s*\((keep-major|keep-minor)\))?", prev_line.strip())
                    if comment_match:
                        repo, option = comment_match.groups()

                if not repo:
                    repo = ENV_REPO_MAP.get(env_var)

                if repo:
                    keep_mode = None if force_relax_keep else option
                    latest_version = get_latest_github_version(
                        repo, 
                        current_version=current_version, 
                        keep_mode=keep_mode
                    )

                if latest_version and latest_version != current_version:
                    line = f"ENV {env_var}_VERSION={latest_version}\n"
                    print(f"🔹 {env_var}: {current_version} → {latest_version}")
                    changes.append(f"{env_var}_VERSION: {current_version} → {latest_version}")
                else:
                    line = original_line

        updated_content.append(line)

    return updated_content, changes

def update_dockerfile(dockerfile_path):
    """Основной процесс: проверка обновлений, сборка, SAST сканирование и подъем версий."""
    docker_namespace = DOCKER_USR or "sanbusrt"
    image_name = f"{docker_namespace}/helm-deployer:latest"
    
    # Шаг 1: Проверка плановых обновлений версий
    print("🔍 Шаг 1: Проверка плановых обновлений версий...")
    updated_content, scheduled_changes = check_and_update_dockerfile(dockerfile_path)
    
    all_changes = list(scheduled_changes)
    
    if scheduled_changes:
        print(f"✅ Обнаружены плановые обновления ({len(scheduled_changes)}):")
        for c in scheduled_changes:
            print(f"  • {c}")
        with open(dockerfile_path, "w") as f:
            f.writelines(updated_content)
    else:
        print("ℹ️ Плановых обновлений не обнаружено.")

    # Шаг 2: Сборка образа для SAST/Vulnerability проверки
    print(f"\n🔨 Шаг 2: Сборка Dockerfile для проверки: {image_name}...")
    build_result = subprocess.run(
        ["docker", "build", "-t", image_name, "."],
        capture_output=True,
        text=True,
        check=False
    )
    if build_result.returncode != 0:
        print("❌ Сборка завершена с ошибкой!")
        log = build_result.stderr or build_result.stdout
        log_tail = log[-1500:] if log else "Нет вывода логов."
        msg = f"❌ <b>Ошибка сборки Dockerfile!</b>\n\n<pre>{html.escape(log_tail)}</pre>"
        send_telegram_notification(msg)
        sys.exit(build_result.returncode)

    print("✅ Сборка успешно завершена.")

    # Шаг 3: SAST / Vulnerability сканирование
    print("\n🛡️ Шаг 3: Запуск SAST сканирования и анализа уязвимостей...")
    scan_report = run_trivy_scan(image_name)
    security_changes = []

    if scan_report:
        crit = scan_report["total_critical"]
        high = scan_report["total_high"]
        vulns_by_comp = scan_report["vulns_by_comp"]
        print(f"📊 Результаты SAST сканирования: CRITICAL={crit}, HIGH={high}")

        if vulns_by_comp:
            print(f"⚠️ Уязвимости обнаружены в компонентах: {', '.join(vulns_by_comp.keys())}")
            print("🚀 Попытка поднять версии уязвимых компонентов в Dockerfile...")
            sec_content, sec_changes = check_and_update_dockerfile(
                dockerfile_path,
                only_components=set(vulns_by_comp.keys()),
                force_relax_keep=False
            )
            
            if sec_changes:
                print(f"✅ Подняты версии уязвимых компонентов ({len(sec_changes)}):")
                for sc in sec_changes:
                    print(f"  • {sc}")
                with open(dockerfile_path, "w") as f:
                    f.writelines(sec_content)
                security_changes.extend(sec_changes)
                all_changes.extend([f"{sc} (Security Fix)" for sc in sec_changes])

                # Пересобираем образ с обновленными компонентами
                print(f"🔨 Пересборка Dockerfile с обновленными компонентами...")
                rebuild_res = subprocess.run(
                    ["docker", "build", "-t", image_name, "."],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if rebuild_res.returncode == 0:
                    print("✅ Пересборка успешна. Повторное сканирование...")
                    scan_report = run_trivy_scan(image_name)
                else:
                    print("❌ Ошибка пересборки после обновления компонентов безопасности.")
            else:
                print("ℹ️ Уязвимые компоненты уже имеют максимально доступные версии в заданных ветках.")

    # Шаг 4: Отправка в Docker Hub (если были изменения и заданы учетные данные)
    pushed = False
    if all_changes and DOCKER_USR and DOCKER_PWD:
        print("\n🔑 Шаг 4: Авторизация в Docker Hub...")
        login_res = subprocess.run(
            ["docker", "login", "-u", DOCKER_USR, "--password-stdin"],
            input=DOCKER_PWD,
            capture_output=True,
            text=True,
            check=False
        )
        if login_res.returncode == 0:
            print(f"📤 Отправка образа в Docker Hub: {image_name}...")
            push_res = subprocess.run(["docker", "push", image_name], capture_output=True, text=True, check=False)
            if push_res.returncode == 0:
                print("✅ Образ успешно отправлен в Docker Hub!")
                pushed = True
            else:
                print(f"❌ Ошибка push в Docker Hub: {push_res.stderr}")
        else:
            print("❌ Ошибка авторизации в Docker Hub.")

    # Шаг 5: Формирование и отправка уведомления в Telegram
    if all_changes or (scan_report and (scan_report["total_critical"] > 0 or scan_report["total_high"] > 0)):
        msg_parts = []
        if all_changes:
            msg_parts.append("✅ <b>Обновления Dockerfile применены!</b>\n")
            msg_parts.append("<b>Изменения:</b>")
            for c in all_changes:
                msg_parts.append(f"• {html.escape(c)}")
        else:
            msg_parts.append("🛡️ <b>Отчёт SAST / Security Scan</b>\n")

        if scan_report:
            msg_parts.append(f"\n<b>Уязвимости:</b> CRITICAL: {scan_report['total_critical']}, HIGH: {scan_report['total_high']}")
            if scan_report["vulns_by_comp"]:
                msg_parts.append("\n<b>Затронутые компоненты:</b>")
                for comp, vlist in scan_report["vulns_by_comp"].items():
                    cve_preview = ", ".join(sorted(list(set(v['cve'] for v in vlist)))[:3])
                    msg_parts.append(f"• <b>{html.escape(comp)}</b> ({len(vlist)} CVE): <code>{html.escape(cve_preview)}</code>")

        if pushed:
            msg_parts.append(f"\n📤 Образ успешно отправлен в Docker Hub: <code>{image_name}</code>")

        send_telegram_notification("\n".join(msg_parts))
    else:
        print("ℹ️ Изменений не обнаружено и критических уязвимостей нет. Сборка завершена.")

# --- Запуск ---
if __name__ == "__main__":
    update_dockerfile(DOCKERFILE_PATH)
