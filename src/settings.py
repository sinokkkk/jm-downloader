"""配置读写：所有设置和运行时数据都放在项目根目录下。"""
import json
import os
from dataclasses import asdict, dataclass

# src/ 的上一级就是项目根目录
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT_DIR, 'config.json')

DEFAULT_SAVE_DIR = os.path.join(ROOT_DIR, 'downloads')

# 运行时产生的临时文件（tempfile / jmcomic 的 debug dump / 工作目录）全部约束到这里
RUNTIME_DIR = os.path.join(ROOT_DIR, '.runtime')
TEMP_DIR = os.path.join(RUNTIME_DIR, 'temp')


@dataclass
class Settings:
    save_dir: str = DEFAULT_SAVE_DIR
    impl: str = 'api'                       # api=APP端(默认, 不限IP) / html=网页端
    dir_rule: str = 'Bd / {Aid}-{Atitle} / images / Ptitle'
    image_suffix: str = ''                  # '' 保留原图(.webp) / '.jpg' / '.png'
    image_threads: int = 32
    # 章节并发：越大越快，但取消时要等所有在途请求返回（8 章时约 15s，2 章时约 1s）。
    # 4 是速度与取消响应的折中值。
    photo_threads: int = 4
    timeout: int = 10                        # 单次请求超时（秒）。图床慢节点常需 6s+，太小会误判失败
    retry_times: int = 4                     # 单个请求失败后的重试次数。图片走图床 CDN，节点多且不稳定，需要足够重试
    proxy_mode: str = 'system'               # system / none / custom
    proxy_value: str = ''                    # 例: 127.0.0.1:7890
    username: str = ''                      # 账号名
    password: str = ''                      # 密码：明文保存到 config.json
    export_pdf: bool = False
    export_zip: bool = False
    export_long_img: bool = False
    font_size: int = 10                      # 界面字号（pt）


def load() -> Settings:
    if not os.path.exists(CONFIG_PATH):
        return Settings()
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as fp:
            data = json.load(fp)
    except Exception:
        return Settings()

    cfg = Settings()
    for key, value in data.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    return cfg


def save(cfg: Settings) -> None:
    with open(CONFIG_PATH, 'w', encoding='utf-8') as fp:
        json.dump(asdict(cfg), fp, ensure_ascii=False, indent=2)


def ensure_runtime_dirs() -> None:
    """创建应用私有运行时目录，用于收纳所有临时产物。"""
    os.makedirs(TEMP_DIR, exist_ok=True)


def apply_runtime_env() -> None:
    """把 Python 与子进程的临时目录、当前工作目录全部收敛到应用目录内。

    必须在 import jmcomic / tkinter 之前调用，否则 tempfile 会缓存旧值。
    """
    ensure_runtime_dirs()
    for name in ('TMP', 'TEMP', 'TMPDIR'):
        os.environ[name] = TEMP_DIR
    # tempfile 模块在首次使用时缓存候选目录，这里主动同步
    import tempfile
    tempfile.tempdir = TEMP_DIR
    # jmcomic 某些分支会把 debug dump / 日志写到 os.getcwd()，统一固定到 runtime 目录。
    # RUNTIME_DIR 是绝对路径，重复调用不会叠加出嵌套目录。
    os.chdir(RUNTIME_DIR)

