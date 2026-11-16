"""下载核心：解析车号、生成配置、后台线程下载，并把结果转成界面事件。"""
import os
import threading
import time
import traceback
from dataclasses import dataclass

import yaml

import jmcomic
from jmcomic import Feature, JmDownloader, JmcomicText
from jmcomic.jm_exception import MissingAlbumPhotoException
from jmcomic.jm_task_context import DownloadControl

import logbook

# 重试也没用的错误（账号密码错、本子不存在），出现后直接停止队列
FATAL_WORDS = ('无效的用户名', '無效的用戶名', '密码错误', '不存在本子', '不存在章节', '401')


def is_fatal(exc):
    if isinstance(exc, MissingAlbumPhotoException):
        return True
    return any(word in str(exc) for word in FATAL_WORDS)


@dataclass
class Job:
    kind: str  # 'album' 本子 / 'photo' 章节
    jm_id: str

    @property
    def label(self):
        return f"{'本子' if self.kind == 'album' else '章节'} {self.jm_id}"


def parse_jobs(text):
    """解析输入框里的车号。p 开头表示章节，自动去重。"""
    jobs = []
    for raw in text.replace(',', ' ').replace('，', ' ').replace('、', ' ').split():
        token = raw.strip()
        if not token:
            continue
        kind = 'album'
        if token[0] in 'pP' and len(token) > 1:
            kind, token = 'photo', token[1:]
        try:
            jm_id = JmcomicText.parse_to_jm_id(token)
        except Exception:
            raise ValueError(f'无法识别车号: {raw}')
        if not any(job.kind == kind and job.jm_id == jm_id for job in jobs):
            jobs.append(Job(kind, jm_id))
    return jobs


def build_option(cfg, domains=None):
    """根据界面设置生成 jmcomic 的 option。"""
    impl = cfg.get('impl', 'api')
    meta = {
        'proxies': _proxy(cfg),
        'timeout': max(3, int(cfg.get('timeout', 10))),  # 不设会用 curl 默认 30 秒
    }
    client = {
        'impl': impl,
        'retry_times': max(0, int(cfg.get('retry_times', 4))),
        'postman': {'meta_data': meta},
    }
    if domains:
        client['domain'] = {impl: list(domains)}

    data = {
        'log': False,
        'client': client,
        'download': {
            'cache': True,
            'image': {'decode': True, 'suffix': cfg.get('image_suffix') or None},
            'threading': {
                'image': max(1, int(cfg.get('image_threads', 32))),
                'photo': max(1, int(cfg.get('photo_threads', 4))),
            },
        },
        'dir_rule': {
            'base_dir': os.path.normpath(cfg.get('save_dir') or '.'),
            # 结构: <车号>-<标题>/images/<章节>/00001.webp
            'rule': cfg.get('dir_rule') or 'Bd / {Aid}-{Atitle} / images / Ptitle',
        },
    }

    username = str(cfg.get('username') or '').strip()
    password = str(cfg.get('password') or '').strip()
    if username and password:
        data['auth'] = {'username': username, 'password': password}

    return jmcomic.create_option_by_str(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def build_features(cfg):
    """下载后自动导出的东西。PDF 放在 <车号>-<标题>/<车号>.pdf。"""
    base_dir = os.path.normpath(cfg.get('save_dir') or '.')

    def rule(ext):
        return {'base_dir': base_dir, 'rule': f'Bd / {{Aid}}-{{Atitle}} / {{Aid}}.{ext}'}

    features = []
    if cfg.get('export_pdf'):
        features.append(Feature.export_pdf(dir_rule=rule('pdf')))
    if cfg.get('export_zip'):
        features.append(Feature.export_zip(dir_rule=rule('zip')))
    if cfg.get('export_long_img'):
        features.append(Feature.export_long_img(dir_rule=rule('png')))
    return features or None


def _proxy(cfg):
    mode = cfg.get('proxy_mode', 'system')
    if mode == 'none':
        return None
    if mode == 'custom':
        return str(cfg.get('proxy_value') or '').strip() or None
    return 'system'


class DownloadWorker(threading.Thread):
    """后台下载线程。进度通过事件队列发给界面。"""

    def __init__(self, jobs, cfg, events):
        super().__init__(daemon=True)
        self.jobs = jobs
        self.cfg = cfg
        self.events = events

        self.stop = threading.Event()        # 中止全部
        self.skip = set()                    # 要跳过的任务下标
        self.abort_current = False           # 只中止当前任务
        self.current_index = -1
        self.control = None                  # 当前任务的取消控制器

        self.done_count = 0
        self.total_count = 0
        self.photo_index = 0
        self.photo_total = 0

        # 当前任务的信息，用于写下载记录
        self.title = ''
        self.photo_count = 0
        self.page_count = 0
        self.save_path = ''

    # ---------- 供界面调用 ----------

    def cancel(self):
        """中止全部任务。"""
        self.stop.set()
        if self.control is not None:
            self.control.cancel('用户取消')
        self.emit('cancelling')

    def abort_job(self, index):
        """中止某个任务：正在下的立即停，还没下的直接跳过。"""
        self.skip.add(index)
        if index == self.current_index and not self.abort_current:
            self.abort_current = True
            if self.control is not None:
                self.control.cancel('用户中止')
            self.emit('job_aborting', index=index)
        else:
            self.emit('job_aborted', index=index)

    def emit(self, kind, **kw):
        self.events.put({'kind': kind, **kw})

    def log(self, message):
        self.emit('log', message=message)
        logbook.write(message)

    # ---------- 主体 ----------

    def run(self):
        from fastnet import fast_domains

        self.log('正在选择最快的 API 域名…')
        domains = fast_domains(_proxy(self.cfg), logger=self.log)
        option = build_option(self.cfg, domains)
        features = build_features(self.cfg)
        downloader = self._make_downloader()

        start = time.perf_counter()
        fatal = None
        for index, job in enumerate(self.jobs):
            if self.stop.is_set():
                break
            if index in self.skip:
                self.emit('job_aborted', index=index)
                continue
            if fatal:
                self.emit('job_skipped', index=index)
                continue

            self.current_index = index
            self.abort_current = False
            self.control = DownloadControl()
            self.save_path = ''
            self.emit('job_start', index=index, label=job.label)

            begin = time.perf_counter()
            try:
                warning = self._download(job, option, downloader, features)
                cost = time.perf_counter() - begin
                if warning:
                    self.emit('job_failed', index=index, message=warning)
                    self._record(job, '部分失败', cost)
                else:
                    self.emit('job_done', index=index, elapsed=cost)
                    self._record(job, '完成', cost)
            except Exception as exc:
                cost = time.perf_counter() - begin
                if self.stop.is_set() or self.abort_current:
                    self.emit('job_failed', index=index, message='已中止')
                    self._record(job, '已中止', cost)
                    if self.stop.is_set():
                        break
                    self.emit('job_aborted', index=index)
                    continue

                self.emit('job_failed', index=index, message=f'{type(exc).__name__}: {exc}')
                self._record(job, '失败', cost)
                if is_fatal(exc):
                    fatal = _fatal_message(exc)
                    self.emit('fatal', message=fatal)
                else:
                    self.log(traceback.format_exc())

        self.emit('worker_finished', elapsed=time.perf_counter() - start,
                  cancelled=self.stop.is_set(), fatal=fatal)

    def _download(self, job, option, downloader, features):
        """下载一个任务。有个别图片失败时返回提示文字，没失败返回 None。"""
        kwargs = dict(option=option, downloader=downloader, extra=features,
                      check_exception=False, login=True, control=self.control)
        api = jmcomic.download_album if job.kind == 'album' else jmcomic.download_photo
        result = api(job.jm_id, **kwargs)

        failed_imgs = getattr(result.downloader, 'download_failed_image', [])
        failed_photos = getattr(result.downloader, 'download_failed_photo', [])
        if failed_imgs or failed_photos:
            return f'部分失败: {len(failed_photos)} 个章节, {len(failed_imgs)} 张图片'
        return None

    def _record(self, job, result, cost):
        logbook.add_history({
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'kind': '本子' if job.kind == 'album' else '章节',
            'jm_id': job.jm_id,
            'title': self.title,
            'photos': self.photo_count,
            'pages': self.page_count,
            'result': result,
            'duration': f'{cost:.1f}s',
            'save_path': self.save_path,
        })

    def _make_downloader(self):
        """自定义下载器，把 jmcomic 的回调转成界面事件。"""
        worker = self

        class GuiDownloader(JmDownloader):
            def before_album(self, album):
                super().before_album(album)
                worker.done_count = 0
                worker.total_count = max(album.page_count, len(album), 1)
                worker.title = album.name
                worker.photo_count = len(album)
                worker.page_count = album.page_count
                worker.save_path = album.save_path
                worker.emit('album_start', title=album.name, photos=len(album),
                            pages=worker.total_count, save_path=album.save_path)

            def before_photo(self, photo):
                super().before_photo(photo)
                # 只下单个章节时不会触发 before_album，用章节自己的数据兜底
                if worker.total_count <= 0:
                    worker.total_count = max(len(photo), 1)
                    worker.title = photo.name
                    worker.photo_count = 1
                    worker.page_count = len(photo)
                    worker.save_path = photo.save_path
                worker.photo_index = photo.index
                worker.photo_total = len(photo.from_album) if photo.from_album else 0

            def after_image(self, image, img_save_path):
                super().after_image(image, img_save_path)
                worker.done_count += 1
                worker.emit('progress', done=worker.done_count, total=worker.total_count,
                            photo_index=worker.photo_index, photo_total=worker.photo_total)

        return GuiDownloader


def _fatal_message(exc):
    text = str(exc)
    if '用户名' in text or '密码' in text or '401' in text:
        return '登录失败：账号或密码错误，请到「设置」里检查。'
    if isinstance(exc, MissingAlbumPhotoException):
        return '本子或章节不存在，请检查车号。'
    return f'无法继续：{text}'


def query_details(jobs, cfg, events):
    """后台查询本子详情。"""
    def run():
        from fastnet import fast_domains

        option = build_option(cfg, fast_domains(_proxy(cfg)))
        client = option.new_jm_client()

        for job in jobs:
            try:
                if job.kind == 'album':
                    item = client.get_album_detail(job.jm_id)
                    chapters, pages = len(item), item.page_count
                else:
                    item = client.get_photo_detail(job.jm_id)
                    chapters, pages = 1, len(item)
                events.put({'kind': 'detail', 'jm_id': job.jm_id,
                            'title': item.name, 'chapters': chapters, 'pages': pages})
            except Exception as exc:
                events.put({'kind': 'detail_failed', 'jm_id': job.jm_id,
                            'message': str(exc)})

        events.put({'kind': 'query_finished'})

    threading.Thread(target=run, daemon=True).start()
