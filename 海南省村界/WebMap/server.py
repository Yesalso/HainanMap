# -*- coding: utf-8 -*-
"""server.py —— 本地预览 + 一键更新服务（省长选举得票地图）

用法：
    python server.py                 # http://127.0.0.1:8765
    python server.py --port 9000
    python server.py --open          # 启动后尝试打开浏览器（可选）

功能：
    1. 把本目录当静态站点提供（Election.html / map.html / *.js / css …），
       并统一加 no-store 响应头，保证刷新页面一定拿到最新 election_data.js。
    2. POST /api/refresh（可选 ?overwrite=1）
       依次执行：
           python fill_votes.py [--overwrite]   # 补齐 投票人数/有效票/废票/候选人得票数/余项得票率
           python build_election.py             # 重新生成 election_data.js
       返回 JSON，供页面「更新数据」按钮使用。
    3. GET /api/ping → 服务信息。

安全：只监听 127.0.0.1（本机），不对局域网/外网开放。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable or "python"
LOCK = threading.Lock()

RE_FILL = re.compile(r"F 投票人数: 新填 (\d+) \| G 有效票: 新填 (\d+) \| 废票: 新填 (\d+) \| 跳过 (\d+) 行")
RE_CAND = re.compile(r"候选人区块: 处理 (\d+) 行")
RE_CNT = re.compile(r"新填得票数 (\d+) 个")
RE_REM = re.compile(r"余项得票率列 (\S+) ")
RE_TOWNS = re.compile(r"有数据的乡镇数: (\d+)")


def run(cmd):
    """跑一条命令，返回 (ok, 合并输出)。"""
    try:
        p = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
    except subprocess.TimeoutExpired:
        return False, "命令超时（>180s）：%s" % " ".join(cmd)
    except Exception as e:                                  # noqa: BLE001
        return False, "无法执行 %s：%s" % (" ".join(cmd), e)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if p.returncode != 0:
        return False, out or ("退出码 %d" % p.returncode)
    return True, out


def do_refresh(overwrite):
    """执行填补 + 重生成数据，返回给前端的 dict。"""
    if not LOCK.acquire(blocking=False):
        return {"ok": False, "error": "已有一次更新正在进行，请稍候"}
    try:
        cmd = [PY, os.path.join(BASE, "fill_votes.py")]
        if overwrite:
            cmd.append("--overwrite")
        ok1, log1 = run(cmd)
        if not ok1:
            hint = ""
            if "Permission" in log1 or "拒绝访问" in log1 or "being used" in log1:
                hint = "（xlsx 可能正被 Excel 打开，请先关闭再试）"
            return {"ok": False, "error": "填补表格失败" + hint, "log": log1}

        ok2, log2 = run([PY, os.path.join(BASE, "build_election.py")])
        if not ok2:
            return {"ok": False, "error": "重新生成 election_data.js 失败", "log": log1 + "\n" + log2}

        m = RE_FILL.search(log1)
        c = RE_CAND.search(log1)
        k = RE_CNT.search(log1)
        t = RE_TOWNS.search(log2)
        parts = []
        if k and int(k.group(1)) > 0:
            parts.append("新填得票數 %s 個" % k.group(1))
        elif c and int(c.group(1)) > 0:
            parts.append("得票數無需補充")
        if c:
            parts.append("處理 %s 行" % c.group(1))
        if m:
            nf, ng, nj = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if nf or ng or nj:
                parts.append("補 投票人數 %d / 有效票 %d / 廢票 %d" % (nf, ng, nj))
        if t:
            parts.append("有數據鄉鎮 %s 個" % t.group(1))
        rem = RE_REM.search(log1)
        if rem and rem.group(1) != "无":
            parts.append("餘項得票率列 " + rem.group(1))
        if not parts:
            parts.append("表格已是最新，無需補充")
        if overwrite:
            parts.insert(0, "（已全量重算）")
        return {"ok": True, "msg": " · ".join(parts), "log": log1 + "\n" + log2,
                "fill": log1, "build": log2}
    finally:
        LOCK.release()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BASE, **kw)

    # ---------- 基础 ----------
    def log_message(self, fmt, *args):        # 静音常规访问日志，只留错误
        if str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    # ---------- 路由 ----------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/ping":
            self.send_json({"ok": True, "base": BASE, "python": PY, "port": self.server.server_port})
            return
        if path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/Election.html")
            self.end_headers()
            return
        super().do_GET()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/api/refresh", "/api/update"):
            if "overwrite" in self._query():
                ow = self._query().get("overwrite", ["0"])[0] not in ("0", "false", "")
            else:
                ow = False
            try:
                self.send_json(do_refresh(ow))
            except Exception as e:                          # noqa: BLE001
                self.send_json({"ok": False, "error": "服务端异常：%s" % e}, 500)
            return
        self.send_json({"ok": False, "error": "未知接口 %s" % path}, 404)


def main():
    ap = argparse.ArgumentParser(description="省长选举得票地图 · 本地预览 + 一键更新服务")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="默认只监听本机")
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%d/Election.html" % (args.host, args.port)
    print("服务目录: %s" % BASE)
    print("地图页面: %s" % url)
    print("更新接口: POST %s/api/refresh   （?overwrite=1 可全量重算）" % ("http://%s:%d" % (args.host, args.port)))
    print("按 Ctrl+C 停止。")
    if args.open:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:                                   # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
