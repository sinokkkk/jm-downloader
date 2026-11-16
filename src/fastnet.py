"""测速挑出可用的 API 域名，并缓存结果。

jmcomic 内置域名列表是随机打乱的，死域名要等 curl 默认 30 秒超时才切换，
这是下载慢的主因。这里启动时并发探测一次，挑出最快的可用域名。
"""
import json
import os
import queue
import threading
import time

from jmcomic import JmModuleConfig

from settings import RUNTIME_DIR

CACHE_FILE = os.path.join(RUNTIME_DIR, 'domain_cache.json')
PROBE_TIMEOUT = 5  # 单个域名探测超时（秒）


def fast_domains(proxies, logger=None):
    """返回可用域名列表（最快的在前）。有缓存时直接返回。"""
    cached = _load_cache()
    if cached:
        if logger:
            logger(f'使用缓存域名: {cached[0]}')
        return cached

    hosts = _all_hosts(proxies)
    if not hosts:
        return []

    if logger:
        logger(f'测速 {len(hosts)} 个域名…')

    usable = _race(hosts, proxies)
    if not usable:
        if logger:
            logger('测速失败，改用内置域名')
        return []

    usable.sort(key=lambda item: item[1])
    ordered = [host for host, _cost in usable]
    if logger:
        logger(f'最快域名: {ordered[0]}')
    _save_cache(ordered)
    return ordered


def clear_cache():
    try:
        os.remove(CACHE_FILE)
    except Exception:
        pass


def _all_hosts(proxies):
    """内置域名 + 官方发布页的最新域名。"""
    hosts = []
    for host in list(JmModuleConfig.DOMAIN_API_UPDATED_LIST or []) + list(JmModuleConfig.DOMAIN_API_LIST):
        host = str(host).strip()
        if host and host not in hosts:
            hosts.append(host)
    for host in _remote_hosts(proxies):
        if host not in hosts:
            hosts.append(host)
    return hosts


def _race(hosts, proxies):
    """并发探测，拿到第一个可用域名就收工，不等死域名。"""
    results = queue.Queue()

    def probe(host):
        results.put((host, _probe(host, proxies)))

    for host in hosts:
        threading.Thread(target=probe, args=(host,), daemon=True).start()

    usable = []
    deadline = time.time() + PROBE_TIMEOUT + 2
    while time.time() < deadline:
        try:
            host, cost = results.get(timeout=0.3)
        except queue.Empty:
            continue
        if cost is not None:
            usable.append((host, cost))
            break
    return usable


def _probe(host, proxies):
    """探测一个域名，可用返回耗时（秒），不可用返回 None。"""
    from curl_cffi import requests
    start = time.perf_counter()
    try:
        resp = requests.get(f'https://{host}/setting', timeout=PROBE_TIMEOUT,
                            impersonate='chrome', proxies=_proxy_dict(proxies))
        # 只有返回 JSON 才算可用，被劫持的域名会返回 HTML 广告页
        json.loads(resp.text)
        return time.perf_counter() - start
    except Exception:
        return None


def _remote_hosts(proxies):
    """从官方发布页拉最新域名列表，失败就返回空。"""
    from curl_cffi import requests
    for url in JmModuleConfig.API_URL_DOMAIN_SERVER_LIST or []:
        try:
            resp = requests.get(url, timeout=PROBE_TIMEOUT, impersonate='chrome',
                                proxies=_proxy_dict(proxies))
            hosts = [line.strip() for line in resp.text.splitlines() if line.strip()]
            if hosts:
                return hosts
        except Exception:
            continue
    return []


def _proxy_dict(proxies):
    """把 'system' / 'clash' 这类写法转成 curl_cffi 认识的 dict。"""
    if proxies is None or isinstance(proxies, dict):
        return proxies
    from jmcomic import ProxyBuilder
    try:
        return ProxyBuilder.build_by_str(proxies)
    except Exception:
        return None


def _load_cache():
    try:
        with open(CACHE_FILE, encoding='utf-8') as fp:
            return [host for host in json.load(fp).get('hosts', []) if host]
    except Exception:
        return []


def _save_cache(hosts):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as fp:
            json.dump({'hosts': hosts}, fp, ensure_ascii=False, indent=2)
    except Exception:
        pass
