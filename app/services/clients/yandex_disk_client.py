# app/services/clients/yandex_disk_client.py

import yadisk
import requests
import logging
import os
import hashlib
from typing import Optional
from .common import UpdateStatus

from config import Config
from app.services.utils.schedule_verification import verify_schedule_file


log = logging.getLogger(__name__)

def _calculate_md5(file_path: str) -> Optional[str]:
    """Вычисляет MD5-хэш локального файла."""
    if not os.path.exists(file_path):
        return None
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        # Читаем файл по частям, чтобы не загружать в память большие файлы
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def update_schedule_file_if_changed(yandex_path: str, local_path: str) -> UpdateStatus:
    """
    Проверяет MD5-хэш и скачивает файл, только если он изменился.
    Возвращает статус операции (SKIPPED, SUCCESS, FAILED).
    """
    temp_path = local_path + ".tmp"

    try:
        y = yadisk.YaDisk(token=Config.YANDEX_TOKEN)

        # --- Блок проверки MD5 ---
        remote_meta = y.get_meta(yandex_path)
        remote_md5 = remote_meta.md5

        if not os.path.exists(local_path):
            log.warning(f"Локальный файл '{local_path}' не найден. Начинаю принудительное скачивание.")
        elif remote_md5 and remote_md5 == _calculate_md5(local_path):
            log.info(f"Хэши для '{yandex_path}' совпадают. Обновление пропущено.")
            return UpdateStatus.SKIPPED
        else:
            log.warning(f"Хэши для '{yandex_path}' отличаются или не удалось их сравнить. Требуется обновление.")

        # --- Блок скачивания ---
        log.info(f"Подключаюсь к Яндекс.Диску для скачивания '{yandex_path}'...")
        y.download(yandex_path, temp_path)
        log.info(f"Файл успешно скачан во временное хранилище: {temp_path}")

        # --- Блок верификации и замены ---
        if not verify_schedule_file(temp_path):
            log.error(f"Скачанный файл '{yandex_path}' не прошел верификацию. Обновление отменено.")
            return UpdateStatus.FAILED

        if os.path.exists(local_path):
            os.remove(local_path)
        os.rename(temp_path, local_path)

        log.info(f"Файл '{yandex_path}' успешно скачан и обновлен в '{local_path}'")
        return UpdateStatus.SUCCESS

    # --- Детальная обработка ошибок ---
    except (requests.exceptions.ConnectionError, yadisk.exceptions.YaDiskConnectionError) as e:
        log.error(f"Сетевая ошибка при работе с Яндекс.Диском: {e}")
        return UpdateStatus.FAILED
    except yadisk.exceptions.PathNotFoundError:
        log.error(f"Файл не найден на Диске по пути: {yandex_path}. Обновление невозможно.")
        return UpdateStatus.FAILED
    except yadisk.exceptions.ForbiddenError as e:
        log.error(f"Нет доступа к файлу на Яндекс.Диске: {yandex_path}. Ошибка: {e}")
        return UpdateStatus.FAILED
    except Exception as e:
        log.critical(f"Произошла непредвиденная ошибка при обновлении файла: {e}", exc_info=True)
        return UpdateStatus.FAILED
    finally:
        # Гарантированная очистка временного файла
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError as e:
                log.error(f"Не удалось удалить временный файл {temp_path}: {e}")