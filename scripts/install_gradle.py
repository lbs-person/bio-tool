"""下载 Gradle 发行版并装进 wrapper 缓存目录。

为什么要用 Python 下：Java 用自带的 cacerts，不认本机中间人代理签发的证书，
所以 gradlew 自己下 Gradle 时会抛 PKIX path building failed。Python 走
Windows 证书存储（truststore），能正常下载。

装到位后 gradlew 会直接复用，不再自己下载。
"""
import os
import sys
import time
import shutil
import zipfile

import requests

try:
    import truststore
    truststore.inject_into_ssl()
except (ImportError, AttributeError):
    # 没有 truststore 时退回默认行为；本机有中间人代理时会因证书失败，
    # 那种情况下需要先 pip install truststore
    pass

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VERSION = "8.14.3"
VARIANT = "all"
# 官方源在本机只有约 70 KB/s（214 MB 要 48 分钟），腾讯云镜像实测 1289 KB/s。
# 两者是同一份发行版，校验一致，可放心换用。
URLS = [
    f"https://mirrors.cloud.tencent.com/gradle/gradle-{VERSION}-{VARIANT}.zip",
    f"https://services.gradle.org/distributions/gradle-{VERSION}-{VARIANT}.zip",
]

CACHE = os.path.join(os.environ["USERPROFILE"], ".gradle", "wrapper", "dists",
                     f"gradle-{VERSION}-{VARIANT}")
# wrapper 用 distributionUrl 的 hash 作为子目录名，已存在的那层可以直接复用
SUBDIRS = [d for d in os.listdir(CACHE)] if os.path.isdir(CACHE) else []
if not SUBDIRS:
    print(f"缓存目录不存在或为空: {CACHE}")
    sys.exit(1)
DEST = os.path.join(CACHE, SUBDIRS[0])
print(f"目标目录: {DEST}")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ZIP = os.path.join(DEST, f"gradle-{VERSION}-{VARIANT}.zip")


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


# ---- 下载 ----
need = True
if os.path.exists(ZIP) and os.path.getsize(ZIP) > 100 * 1024 * 1024:
    print(f"zip 已存在 {human(os.path.getsize(ZIP))}，跳过下载")
    need = False

if need:
    ok = False
    for url in URLS:
        print(f"下载 {url}")
        try:
            r = requests.get(url, headers=UA, timeout=180, stream=True,
                             allow_redirects=True)
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            t0 = time.perf_counter()
            last = 0.0
            with open(ZIP + ".part", "wb") as f:
                for chunk in r.iter_content(1024 * 256):
                    f.write(chunk)
                    got += len(chunk)
                    now = time.perf_counter()
                    if now - last >= 3:
                        last = now
                        rate = got / max(0.01, now - t0)
                        eta = f"{(total-got)/rate/60:5.1f} 分" if total and rate else "?"
                        sys.stdout.write(f"\r    {100.0*got/total:5.1f}%  "
                                         f"{human(got)}/{human(total)}  "
                                         f"{rate/1024:6.0f} KB/s  剩 {eta}   ")
                        sys.stdout.flush()
            if total and got < total * 0.98:
                raise IOError(f"只下到 {human(got)}，预期 {human(total)}")
            shutil.move(ZIP + ".part", ZIP)
            print(f"\r    完成 {human(got)}  用时 {(time.perf_counter()-t0)/60:.1f} 分"
                  f"  平均 {got/(time.perf_counter()-t0)/1024:.0f} KB/s          ")
            ok = True
            break
        except Exception as e:
            print(f"\r    失败: {str(e)[:100]}")
            if os.path.exists(ZIP + ".part"):
                os.remove(ZIP + ".part")
            continue
    if not ok:
        sys.exit("所有源都失败")

# ---- 解压 ----
inner = f"gradle-{VERSION}"
target = os.path.join(DEST, inner)
if os.path.isdir(target):
    print(f"已解压: {target}")
else:
    print("解压 …")
    with zipfile.ZipFile(ZIP) as z:
        z.extractall(DEST)
    print(f"  -> {target}")

# ---- 写 .ok 标记，让 wrapper 认为分发版已就绪 ----
ok = os.path.join(DEST, f"gradle-{VERSION}-{VARIANT}.zip.ok")
with open(ok, "w") as f:
    f.write("")
print(f"标记文件: {ok}")

# ---- 清掉 wrapper 留下的锁与残片 ----
for junk in (f"gradle-{VERSION}-{VARIANT}.zip.part",
             f"gradle-{VERSION}-{VARIANT}.zip.lck"):
    p = os.path.join(DEST, junk)
    if os.path.exists(p):
        os.remove(p)
        print(f"清理 {junk}")

print()
print("gradle 可执行文件:", os.path.exists(os.path.join(target, "bin", "gradle.bat")))
print("发行版目录内容:", os.listdir(DEST))
