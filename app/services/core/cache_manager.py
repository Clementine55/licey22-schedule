# app/services/core/cache_manager.py

import json
import logging
import os
import time
from typing import Tuple
from threading import Lock

from config import Config, BASE_DIR
from app.utils import make_json_serializable

from .backup_manager import create_backup, clean_old_backups, get_latest_backup_path

from app.services.clients.time_service import get_current_day_and_time
from app.services.clients import yandex_disk_client
from app.services.clients import seafile_client

from app.services.utils.excel_reader import open_excel_file
from app.services.utils.schedule_comparator import compare_schedules
from app.services.utils.enums import DayType
from app.services.clients.common import UpdateStatus

from app.services.parsers.short_day_parser import get_short_days_from_file
from app.services.parsers.schedule_parser import parse_schedule
from app.services.parsers.consultation_parser import parse_consultations
from app.services.parsers.landscape_builder import build_landscape_view
from app.services.parsers.portrait_builder import build_portrait_view


log = logging.getLogger(__name__)
thread_lock = Lock()


def get_schedule_data(schedule_name: str, force_update: bool = False) -> dict:
    """
    Главная функция. Получает расписание из кэша или запускает его обновление.

    :param schedule_name: Имя расписания из конфига.
    :param force_update: Флаг для принудительного обновления, игнорируя CACHE_DURATION.
    """
    if schedule_name not in Config.SCHEDULES:
        return {"error": "Schedule not found"}

    cache_file = os.path.join(BASE_DIR, 'data', f'{schedule_name}_cache.json')

    try:
        data_dir = os.path.join(BASE_DIR, 'data')
        os.makedirs(data_dir, exist_ok=True)
    except OSError as e:
        log.critical(f"Критическая ошибка: не удалось создать директорию '{data_dir}': {e}")
        return {"error": f"Не удалось создать рабочую директорию: {e}"}


    is_cache_stale = False
    try:
        if (time.time() - os.path.getmtime(cache_file)) > Config.CACHE_DURATION:
            is_cache_stale = True
            log.warning(f"Кэш для '{schedule_name}' устарел по времени (первичная проверка).")
    except FileNotFoundError:
        is_cache_stale = True
        log.warning(f"Кэш для '{schedule_name}' отсутствует (первичная проверка).")

    # Единая точка принятия решения
    if is_cache_stale or force_update:
        if force_update:
            log.warning(f"Принудительное обновление кэша для '{schedule_name}' инициировано.")

        with thread_lock:
            # --- ВОТ ГЛАВНОЕ ИСПРАВЛЕНИЕ ---
            # Повторно проверяем, не обновил ли кто-то кэш, пока мы ждали блокировку.
            try:
                is_still_stale = (time.time() - os.path.getmtime(cache_file)) > Config.CACHE_DURATION
            except FileNotFoundError:
                is_still_stale = True  # Если файла все еще нет, значит, он все еще "устарел"

            if is_still_stale or force_update:
                log.info(f"Блокировка получена. Начинаю обновление кэша для '{schedule_name}'.")
                success, message = _update_cache_file(schedule_name, cache_file)
                if not success:
                    return {"error": message}
            else:
                log.info(f"Блокировка получена, но кэш уже обновлен другим процессом. Обновление пропущено.")

    # ---> 3. Чтение из файла кэша <---
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            log.info(f"Загрузка данных для '{schedule_name}' из файла кэша.")
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        error_message = f"Критическая ошибка: не удалось прочитать файл кэша для '{schedule_name}'. {e}"
        log.error(error_message)
        return {"error": error_message}


def _update_cache_file(schedule_name: str, cache_file: str) -> Tuple[bool, str]:
    """
    Внутренняя функция для скачивания, парсинга и сохранения данных в кэш.
    """

    # --- ШАГ 1: ПРОВЕРЯЕМ И ОБНОВЛЯЕМ ФАЙЛ С СЕРВЕРА ---
    schedule_config = Config.SCHEDULES[schedule_name]
    remote_path = schedule_config['remote_path']
    local_path = schedule_config['local_path']

    if Config.CLOUD_SERVICE == 'LIS':
        update_status = seafile_client.update_schedule_file_if_changed(
            schedule_config['repo_id'], remote_path, local_path
        )
    else:
        update_status = yandex_disk_client.update_schedule_file_if_changed(
            remote_path, local_path
        )


    # --- ШАГ 2: ОБРАБАТЫВАЕМ РЕЗУЛЬТАТ ОБНОВЛЕНИЯ ---

    # Сценарий 1: Файл не менялся, всё хорошо, выходим.
    if update_status == UpdateStatus.SKIPPED:
        # Теперь ПРОВЕРЯЕМ, нужно ли нам что-то делать.
        if os.path.exists(cache_file):
            # Если кэш ЕСТЬ, то делать ничего не нужно. Просто "освежаем" его и выходим.
            os.utime(cache_file, None)
            return True, "Удаленный файл не изменился. Обновление кэша пропущено."
        else:
            # А вот и наш случай! Файл Excel не менялся, но кэша нет.
            # Мы обязаны его создать.
            log.warning("Файл Excel не изменился, но кэш отсутствует. Запускаю принудительный парсинг.")
            # Мы НЕ выходим из функции, а просто "проваливаемся" дальше к коду парсинга.

    # Сценарий 2: Файл был успешно обновлен.
    elif update_status == UpdateStatus.SUCCESS:
        log.info("Файл был обновлен. Запускаю бэкап, сравнение и парсинг.")

        # --- ВОЗВРАЩАЕМ ЛОГИКУ БЭКАПА И СРАВНЕНИЯ ---
        try:
            # Находим последний бэкап для сравнения
            latest_backup_path = get_latest_backup_path(schedule_name, local_path)

            # Создаем новый бэкап уже из обновленного файла
            create_backup(schedule_name, local_path)

            # Очищаем старые бэкапы
            base_data_dir = os.path.dirname(local_path)
            clean_old_backups(schedule_name, base_data_dir)

            # Сравниваем новый файл с последним бэкапом
            if latest_backup_path:
                changes = compare_schedules(old_file_path=latest_backup_path, new_file_path=local_path)
                if changes:
                    log.warning(f"Обнаружены изменения в расписании '{schedule_name}': {changes}")
        except Exception as e:
            log.error(f"Ошибка при бэкапе или сравнении для '{schedule_name}': {e}", exc_info=True)
        # --- КОНЕЦ БЛОКА БЭКАПА ---

    # Сценарий 3: Произошла ошибка при обновлении.
    elif update_status == UpdateStatus.FAILED:
        if os.path.exists(local_path):
            log.warning(f"Не удалось обновить файл для '{schedule_name}'. Используется старая локальная копия.")
            # Мы не выходим, а продолжаем, чтобы распарсить старый файл
        else:
            msg = f"Критическая ошибка: не удалось ни обновить, ни найти локальный файл для '{schedule_name}'."
            log.critical(msg)
            return False, msg  # Здесь точно выходим, парсить нечего

    # --- ШАГ 3: ПАРСИНГ ЛОКАЛЬНОГО ФАЙЛА (нового или старого) ---
    log.info(f"Парсинг всех данных для '{schedule_name}'...")
    all_data = _parse_all_data(schedule_name)
    if all_data.get("error"):
        return False, all_data["error"]

    # --- ШАГ 4: СОХРАНЕНИЕ В КЭШ ---
    temp_cache_file = cache_file + ".tmp"
    with open(temp_cache_file, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    os.replace(temp_cache_file, cache_file)
    msg = f"Кэш для '{schedule_name}' успешно обновлен."
    log.info(msg)
    return True, msg

def _parse_all_data(schedule_name: str) -> dict:
    """Парсит и уроки, и консультации, собирая финальную структуру для кэша."""
    local_path = Config.SCHEDULES[schedule_name]['local_path']
    if not os.path.exists(local_path):
        return {"error": "Local schedule file not found"}

    # --- ИЗМЕНЕНИЕ: ЛОГИКА СБОРКИ ---

    log.info(f"Открываем файл '{local_path}' ОДИН РАЗ для всех парсеров.")

    xls = open_excel_file(local_path)
    if not xls:
        error_msg = f"Не удалось открыть Excel файл через excel_reader: {local_path}"
        log.error(error_msg)
        return {"error": error_msg}

    try:
        # Передаем ОТКРЫТЫЙ ФАЙЛ в парсеры
        short_days_list = get_short_days_from_file(xls)
        current_time_info = get_current_day_and_time()
        is_short_day_today = current_time_info.date_str_iso in short_days_list

        day_type_for_parser = DayType.SHORT if is_short_day_today else DayType.NORMAL
        log.info(f"Определен тип дня для парсинга: '{day_type_for_parser.name}'")

        raw_lessons = parse_schedule(xls, day_type_override=day_type_for_parser)
        consultations = parse_consultations(xls)

        # 3. Собираем финальные структуры данных, как они были раньше
        schedule_normal = {}
        schedule_short = {}
        days_order = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]

        schedule = {}
        for day in days_order:
            daily_lessons = raw_lessons.get(day, [])
            schedule[day] = {
                "portrait_view": build_portrait_view(daily_lessons),
                "landscape_slides": build_landscape_view(daily_lessons)
            }

    finally:
        # Этот блок гарантирует, что файл будет закрыт, даже если при парсинге произойдет ошибка
        xls.close()

    return make_json_serializable({
        "schedule": schedule,
        "consultations": consultations,
    })