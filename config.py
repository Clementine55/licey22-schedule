import os
from dotenv import load_dotenv

# Определяем путь к файлу .env.

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Используем BASE_DIR для поиска файла .env
load_dotenv(os.path.join(BASE_DIR, '.env'))

class Config:
    CLOUD_SERVICE = os.getenv('CLOUD_SERVICE', 'YA').upper()

    YANDEX_TOKEN = os.getenv('YANDEX_TOKEN')

    SEAFILE_URL = os.getenv('SEAFILE_URL')
    SEAFILE_TOKEN = os.getenv('SEAFILE_TOKEN')

    SCHEDULES = {}
    i = 1
    while True:
        file_name_key = os.getenv(f'FILE_NAME_{i}')
        if not file_name_key:
            break

        if CLOUD_SERVICE == 'LIS':
            remote_path = os.getenv(f'LIS_FILE_PATH_{i}')
            repo_id = os.getenv(f'LIS_REPO_ID_{i}')
            if not remote_path or not repo_id:
                break
            SCHEDULES[file_name_key] = {
                'remote_path': remote_path,
                'repo_id': repo_id,
                'local_path': os.path.join(BASE_DIR, 'data', f'{file_name_key}.xlsx'),
            }
        if CLOUD_SERVICE == 'YA':
            remote_path = os.getenv(f'YANDEX_FILE_PATH_{i}')
            if not remote_path:
                break
            SCHEDULES[file_name_key] = {
                'remote_path': remote_path,
                'local_path': os.path.join(BASE_DIR, 'data', f'{file_name_key}.xlsx'),
            }
        else:
            break
        i += 1

    LOGO_FILE_PATH = os.getenv('LOGO_FILE_PATH', 'img/logo.png')
    CACHE_DURATION = int(os.getenv('CACHE_DURATION', 600))
    CAROUSEL_INTERVAL = int(os.getenv('CAROUSEL_INTERVAL', 7))
    SHOW_BEFORE_START_MIN = int(os.getenv('SHOW_BEFORE_START_MIN', 75))
    SHOW_AFTER_END_MIN = int(os.getenv('SHOW_AFTER_END_MIN', 30))
    REGION_TIMEDELTA = int(os.getenv('REGION_TIMEDELTA', 7))
    BACKUP_RETENTION_DAYS = int(os.getenv('BACKUP_RETENTION_DAYS', 7))


    if CLOUD_SERVICE == 'YA' and not YANDEX_TOKEN:
        raise ValueError("CLOUD_SERVICE=YA, но YANDEX_TOKEN не задан в .env")
    if CLOUD_SERVICE == 'LIS' and not (SEAFILE_URL and SEAFILE_TOKEN):
        raise ValueError("CLOUD_SERVICE=LIS, но SEAFILE_URL/SEAFILE_TOKEN не заданы в .env")
    if CLOUD_SERVICE != 'LIS' and CLOUD_SERVICE != 'YA':
        raise ValueError("CLOUD_SERVICE=Undefined, выберите YA или LIS в .env")
    if not SCHEDULES:
        raise ValueError("Не найдено ни одной конфигурации расписания в .env")