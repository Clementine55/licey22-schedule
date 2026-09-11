# app/services/clients/seafile_client.py

import requests
import logging
import os
from .common import UpdateStatus

from config import Config
from app.services.utils.schedule_verification import verify_schedule_file

log = logging.getLogger(__name__)

def _get_file_detail(repo_id: str, seafile_path: str) -> dict | None:
    """Возвращает метаданные файла в Seafile: id (хэш содержимого), mtime, size."""
    url = f"{Config.SEAFILE_URL}/api2/repos/{repo_id}/file/detail/"
    headers = {"Authorization": f"Token {Config.SEAFILE_TOKEN}"}
    resp = requests.get(url, headers=headers, params={"p": seafile_path}, timeout=15)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _get_download_link(repo_id: str, seafile_path: str) -> str:
    url = f"{Config.SEAFILE_URL}/api2/repos/{repo_id}/file/"
    headers = {"Authorization": f"Token {Config.SEAFILE_TOKEN}"}
    resp = requests.get(url, headers=headers, params={"p": seafile_path}, timeout=15)
    resp.raise_for_status()
    # API возвращает URL в кавычках как строку
    return resp.json() if isinstance(resp.json(), str) else resp.text.strip('"')


def update_schedule_file_if_changed(repo_id: str, seafile_path: str, local_path: str) -> UpdateStatus:
    """
    Аналог yandex-клиента, но для Seafile.
    Сравнение по 'id' файла в Seafile (это content-hash, свой аналог MD5),
    который кэшируем рядом с локальным файлом в sidecar '.meta'.
    """
    temp_path = local_path + ".tmp"
    meta_path = local_path + ".meta"

    try:
        detail = _get_file_detail(repo_id, seafile_path)
        if detail is None:
            log.error(f"Файл не найден в Seafile по пути: {seafile_path}")
            return UpdateStatus.FAILED

        remote_id = detail.get("id")

        local_id = None
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                local_id = f.read().strip()

        if not os.path.exists(local_path):
            log.warning(f"Локальный файл '{local_path}' не найден. Принудительное скачивание.")
        elif remote_id and remote_id == local_id:
            log.info(f"Файл '{seafile_path}' не изменился (id совпадает). Обновление пропущено.")
            return UpdateStatus.SKIPPED
        else:
            log.warning(f"Содержимое файла '{seafile_path}' изменилось. Требуется обновление.")

        download_url = _get_download_link(repo_id, seafile_path)
        file_resp = requests.get(download_url, timeout=60)
        file_resp.raise_for_status()

        with open(temp_path, "wb") as f:
            f.write(file_resp.content)

        if not verify_schedule_file(temp_path):
            log.error(f"Скачанный файл '{seafile_path}' не прошел верификацию. Обновление отменено.")
            return UpdateStatus.FAILED

        if os.path.exists(local_path):
            os.remove(local_path)
        os.rename(temp_path, local_path)

        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(remote_id or "")

        log.info(f"Файл '{seafile_path}' успешно скачан и обновлен в '{local_path}'")
        return UpdateStatus.SUCCESS

    except requests.exceptions.ConnectionError as e:
        log.error(f"Сетевая ошибка при работе с Seafile: {e}")
        return UpdateStatus.FAILED
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            log.error(f"Токен Seafile недействителен или истёк: {e}")
        else:
            log.error(f"HTTP-ошибка при работе с Seafile: {e}")
        return UpdateStatus.FAILED
    except Exception as e:
        log.critical(f"Непредвиденная ошибка при обновлении файла из Seafile: {e}", exc_info=True)
        return UpdateStatus.FAILED
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError as e:
                log.error(f"Не удалось удалить временный файл {temp_path}: {e}")