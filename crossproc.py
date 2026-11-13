# -*- coding: utf-8 -*-
"""crossproc.py — 跨进程名额/互斥(纯标准库, 文件系统实现)。

为什么要有这一层(2026-11-13 用户要"并发 5 篇"):
  并发单元是**独立进程**(stages.run 一篇一子进程), 进程内的 threading 锁彼此看不见。
  而真正会出问题的不是 CPU, 是"共享且失败时静默"的外部资源:

    LLM      — 同一 key 并发过高会限流。ner_lite 单批网络错误只记一条 nomination 就继续,
               结果是"跑完了但少抽了"——静默失败, 正是最难发现的那种。
    渠道 API — dots/paddle 并发过高同理;限流后该路直接变成 None, 产物里看不出"这篇少了一个渠道"。

  (MinerU 桌面版 CDP 驱动已于 2026-11-13 移除, 那一路的互斥名额随之取消——现在 MinerU 只走云端 API。)

  所以并发必须带闸: 资源能开几个就开几个, 不许"能起进程就全上"。

实现取舍:
  槽位文件不是锁, 是**占位凭证**: 进程崩了 O_EXCL 创建的文件不会自动消失, 所以必须有 ttl;
  ttl 不能短(全链 250s 有实跑记录), 等待也不能无限(卡住比失败更糟)。
  因此: 抢不到就等, 等满 wait 秒**降级继续跑**并留一行日志 —— 宁可慢, 不要静默卡死。
  独占创建由内核保证原子(Windows/Linux 都是), 两进程同时抢同一槽只有一个能 O_EXCL 成功。
"""
import io
import os
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK_ROOT = os.path.join(HERE, ".locks")


def _slot_dir(name):
    d = os.path.join(LOCK_ROOT, "".join(c if (c.isalnum() or c in "-_") else "_" for c in name))
    os.makedirs(d, exist_ok=True)
    return d


def _alive(fp, ttl):
    """槽文件是否还有效。超 ttl 视为死进程遗留。"""
    try:
        return (time.time() - os.path.getmtime(fp)) < ttl
    except OSError:
        return False


class Slots(object):
    """跨进程名额: 最多 n 个进程同时进入。用法 with Slots("llm", n=2): ...

    name  槽目录名(同一资源必须同名)
    n     名额数
    ttl   占位存活上限秒; 持有者跑超过 ttl 也不会被抢(每次进入后不续期, 只按创建时刻算),
          因此 ttl 要大于单篇最长耗时, 否则长任务会被误判为死进程而超额。
    wait  最多等多久, 超时降级继续(记 degraded=True, 由调用方决定是否提示)
    """

    def __init__(self, name, n=1, ttl=3600, wait=900):
        self.name, self.n, self.ttl, self.wait = name, max(1, int(n)), ttl, wait
        self.dir = _slot_dir(name)
        self.fp = None
        self.degraded = False
        self.waited = 0.0

    # --- 内部 ---
    def _try(self):
        os.makedirs(self.dir, exist_ok=True)
        for i in range(self.n):
            fp = os.path.join(self.dir, "slot_%d" % i)
            try:
                fd = os.open(fp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if not _alive(fp, self.ttl):
                    try:
                        os.remove(fp)      # 死进程遗留: 清掉后本轮重试
                        fd = os.open(fp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    except OSError:
                        continue
                else:
                    continue
            except OSError:
                continue
            try:
                os.write(fd, ("%d %s %.0f" % (os.getpid(), uuid.uuid4().hex[:8], time.time())).encode())
            except OSError:
                pass
            finally:
                os.close(fd)
            self.fp = fp
            return True
        return False

    def _clean_stale(self):
        try:
            for f in os.listdir(self.dir):
                if f.startswith("slot_"):
                    fp = os.path.join(self.dir, f)
                    if not _alive(fp, self.ttl):
                        try:
                            os.remove(fp)
                        except OSError:
                            pass
        except OSError:
            pass

    # --- 对外 ---
    def __enter__(self):
        t0 = time.time()
        # 退避上限刻意压到 0.8s: 这里等的都是"另一篇跑完释放", 不是抢不到的忙等。
        # 上限太大时(实测 2.0s)排队第三批多等 1s 以上, 5 篇并发时就白白损失十几秒。
        delay = 0.1
        while True:
            if self._try():
                self.waited = time.time() - t0
                return self
            if time.time() - t0 >= self.wait:
                self.degraded = True
                self.waited = time.time() - t0
                return self
            if int(time.time() - t0) % 7 == 0:
                self._clean_stale()
            time.sleep(delay)
            delay = min(0.8, delay * 1.3)

    def __exit__(self, *exc):
        if self.fp:
            try:
                os.remove(self.fp)
            except OSError:
                pass
            self.fp = None
        return False


def describe():
    """当前各槽占用(诊断用): {name: [pid 文件...]}"""
    out = {}
    if not os.path.isdir(LOCK_ROOT):
        return out
    for name in sorted(os.listdir(LOCK_ROOT)):
        d = os.path.join(LOCK_ROOT, name)
        if not os.path.isdir(d):
            continue
        rows = []
        for f in sorted(os.listdir(d)):
            fp = os.path.join(d, f)
            try:
                with io.open(fp, encoding="utf-8", errors="replace") as fh:
                    rows.append(fh.read(120).strip())
            except OSError:
                rows.append("?")
        out[name] = rows
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(describe(), ensure_ascii=False, indent=1))
