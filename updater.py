#!/usr/bin/env python3
import re
import urllib.request
import urllib.error
import json
import os
import subprocess
import html

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
    "KUBEDOG": "werf/kubedog"
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
        "parse_mode": "HTML"
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


# --- Функции ---
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

def get_latest_github_version(repo, prefix="v", current_version=None, keep_major=False):
    """Получить последнюю стабильную версию с GitHub (семантически наибольшую)."""
    url = f"https://api.github.com/repos/{repo}/releases"
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
                    # Функция для семантического сравнения версий по числам
                    def semver_key(v):
                        parts = re.findall(r"\d+", v)
                        return tuple(map(int, parts)) if parts else (0,)
                    
                    versions.sort(key=semver_key, reverse=True)
                    
                    # Если нужно ограничиться текущей мажорной версией
                    if keep_major and current_version:
                        current_major = semver_key(current_version)[0]
                        filtered = [v for v in versions if semver_key(v)[0] == current_major]
                        if filtered:
                            return filtered[0]
                            
                    return versions[0]
    except Exception as e:
        print(f"❌ Ошибка запроса GitHub для {repo}: {e}")
        return None
    print(f"❌ Не найден стабильный релиз для {repo}.")
    return None

def update_dockerfile(dockerfile_path):
    """Обновить Dockerfile и вывести, какие версии изменились."""
    with open(dockerfile_path, "r") as file:
        dockerfile_content = file.readlines()

    updated_content = []
    latest_alpine_version = get_latest_alpine_version()
    if latest_alpine_version:
        print(f"✅ Последняя стабильная версия Alpine: {latest_alpine_version}")

    changes = []
    for i, line in enumerate(dockerfile_content):
        original_line = line

        # --- Обновляем Alpine ---
        if line.startswith("FROM alpine:") and latest_alpine_version:
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

            # Ищем репозиторий и опции на предыдущей строке-комментарии
            prev_line = dockerfile_content[i - 1] if i > 0 else ""
            repo = None
            option = None
            if prev_line.strip().startswith("# github:"):
                comment_match = re.match(r"^#\s*github:\s*([\w.-]+/[\w.-]+)(?:\s*\((keep-major)\))?", prev_line.strip())
                if comment_match:
                    repo, option = comment_match.groups()

            # Если репозиторий не указан в комментарии, берем из дефолтной мапы
            if not repo:
                repo = ENV_REPO_MAP.get(env_var)

            if repo:
                keep_major = (option == "keep-major")
                latest_version = get_latest_github_version(
                    repo, 
                    current_version=current_version, 
                    keep_major=keep_major
                )

            if latest_version and latest_version != current_version:
                line = f"ENV {env_var}_VERSION={latest_version}\n"
                print(f"🔹 {env_var}: {current_version} → {latest_version}")
                changes.append(f"{env_var}_VERSION: {current_version} → {latest_version}")
            else:
                line = original_line

        updated_content.append(line)

    if changes:
        # --- Сохраняем Dockerfile ---
        with open(dockerfile_path, "w") as file:
            file.writelines(updated_content)

        print("\n✅ Dockerfile обновлён успешно!")

        # --- Запускаем сборку Docker ---
        image_name = "sanbusrt/helm-deployer:latest"
        print(f"🔨 Запуск сборки Dockerfile: {image_name}...")
        try:
            build_result = subprocess.run(
                ["docker", "build", "-t", image_name, "."],
                capture_output=True,
                text=True,
                check=False
            )
            
            if build_result.returncode == 0:
                print("✅ Сборка завершена успешно!")
                pushed = False
                if DOCKER_USR and DOCKER_PWD:
                    print("🔑 Авторизация в Docker Hub...")
                    login_result = subprocess.run(
                        ["docker", "login", "-u", DOCKER_USR, "--password-stdin"],
                        input=DOCKER_PWD,
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if login_result.returncode != 0:
                        raise Exception(f"Ошибка авторизации в Docker: {login_result.stderr or login_result.stdout}")
                    print("✅ Авторизация успешна!")

                    print(f"📤 Отправка образа в Docker Hub: {image_name}...")
                    push_result = subprocess.run(
                        ["docker", "push", image_name],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if push_result.returncode != 0:
                        raise Exception(f"Ошибка push в Docker Hub: {push_result.stderr or push_result.stdout}")
                    print("✅ Образ успешно отправлен!")
                    pushed = True

                msg = (
                    "✅ <b>Сборка Dockerfile успешна!</b>\n\n"
                    "Обнаружены и применены изменения:\n" +
                    "\n".join(f"• {c}" for c in changes)
                )
                if pushed:
                    msg += f"\n\n📤 Образ успешно отправлен в Docker Hub: <code>{image_name}</code>"
                send_telegram_notification(msg)
            else:
                print("❌ Сборка завершена с ошибкой!")
                # Получаем логи сборки
                log = build_result.stderr if build_result.stderr else build_result.stdout
                log_tail = log[-1500:] if log else "Нет вывода логов."
                escaped_log = html.escape(log_tail)
                
                msg = (
                    "❌ <b>Сборка Dockerfile завершилась с ошибкой!</b>\n\n"
                    "Попытка применить изменения:\n" +
                    "\n".join(f"• {c}" for c in changes) +
                    f"\n\n<b>Последние строки лога сборки:</b>\n<pre>{escaped_log}</pre>"
                )
                send_telegram_notification(msg)
                import sys
                sys.exit(build_result.returncode)
        except Exception as e:
            print(f"❌ Ошибка во время сборки/отправки Docker: {e}")
            msg = (
                "❌ <b>Критическая ошибка сборки/отправки Dockerfile!</b>\n\n"
                "Попытка применить изменения:\n" +
                "\n".join(f"• {c}" for c in changes) +
                f"\n\n<b>Ошибка:</b> {str(e)}"
            )
            send_telegram_notification(msg)
            import sys
            sys.exit(1)
    else:
        print("ℹ️ Изменений не обнаружено. Сборка не требуется.")

# --- Запуск ---
if __name__ == "__main__":
    update_dockerfile(DOCKERFILE_PATH)