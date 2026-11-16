"""日志和下载记录。每次下载写一个独立的日志文件。"""
import json
import os
import threading
import time

from settings import RUNTIME_DIR

LOG_DIR = os.path.join(RUNTIME_DIR, 'logs')
HISTORY_FILE = os.path.join(RUNTIME_DIR, 'history.json')
MAX_HISTORY = 500

_lock = threading.Lock()
_session_file = None  # 当前下载会话的日志文件，None 表示没在下载


def start_session(label=''):
    """开始一次下载，之后日志写进这个新文件。"""
    global _session_file
    os.makedirs(LOG_DIR, exist_ok=True)
    name = time.strftime('%Y%m%d-%H%M%S')
    _session_file = os.path.join(LOG_DIR, f'jm-{name}{"-" + label if label else ""}.log')
    write(f'===== 下载开始 {"· " + label if label else ""} =====')
    return _session_file


def end_session():
    """结束会话，日志回落到按日期命名。"""
    global _session_file
    if _session_file:
        write('===== 下载结束 =====')
        _session_file = None


def log_path():
    if _session_file:
        return _session_file
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, f'jm-{time.strftime("%Y%m%d")}.log')


def write(message):
    line = f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {message}\n'
    try:
        with _lock:
            with open(log_path(), 'a', encoding='utf-8') as fp:
                fp.write(line)
    except Exception:
        pass


# ---------------- 下载记录 ----------------

def load_history():
    try:
        with open(HISTORY_FILE, encoding='utf-8') as fp:
            return json.load(fp)
    except Exception:
        return []


def add_history(record):
    with _lock:
        records = [record] + load_history()
        try:
            os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
            with open(HISTORY_FILE, 'w', encoding='utf-8') as fp:
                json.dump(records[:MAX_HISTORY], fp, ensure_ascii=False, indent=2)
        except Exception:
            pass


def clear_history():
    with _lock:
        try:
            os.remove(HISTORY_FILE)
        except Exception:
            pass
