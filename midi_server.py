#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MIDI Web 监控面板（前后端分离） v1.1
=====================================
后端：读键盘 → 白名单过滤 → 转发 loopMIDI → SSE 实时推流给浏览器
前端：浏览器打开 http://127.0.0.1:8765
      顶部 LED 电平跳线条 + 底部 cmd 风格实时终端输出

白名单（只放行到 DAW）：note(击键) / CC1(调制轮) / CC64(踏板)
丢弃但显示        ：弯音 pitchwheel / CC7(音量) / 其它一切杂信号

运行：双击 监控面板web.bat  （或 python midi_server.py）
"""
import os
import sys
import time
import json
import queue
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mido
import midi_pitch_filter as F   # 复用 CC 名称/力度条等

HOST = "127.0.0.1"
PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(HERE, "webui.html")


# ================= 后端 MIDI 引擎（白名单过滤 + 转发 + 推流） =================
class Engine(threading.Thread):
    def __init__(self, q):
        super().__init__(daemon=True)
        self.q = q
        self._stop = False

    def stop(self):
        self._stop = True

    @staticmethod
    def hdr():
        return time.strftime("[%H:%M:%S]")

    def emit(self, data):
        self.q.put(data)

    def line(self, text, extra=None):
        """打印到 cmd 控制台 + 推送一条 cmd 风格终端行给网页；
        extra 为同消息的结构化电平数据（可选）。"""
        full = self.hdr() + "  " + text
        print(full, flush=True)          # cmd 黑窗（控制台）
        self.emit({"line": full})        # 网页终端
        if extra:
            self.emit(extra)

    def find_ports(self):
        try:
            ins = [n for n in mido.get_input_names() if "loopMIDI" not in n]
            outs = [n for n in mido.get_output_names() if "loopMIDI" in n]
            return (ins[0] if ins else None), (outs[0] if outs else None)
        except Exception:
            return None, None

    def run(self):
        while not self._stop:
            try:
                inn, outn = self.find_ports()
                if not inn or not outn:
                    self.line("等待键盘 / loopMIDI 端口...")
                    time.sleep(2)
                    continue
                with mido.open_input(inn) as inp, mido.open_output(outn) as outp:
                    self.line(f"连接成功: {inn}  ->  {outn}")
                    self.line("开始处理 MIDI... (Ctrl+C 退出，拔插键盘自动恢复)")
                    while not self._stop:
                        msg = inp.poll()
                        if msg is None:
                            time.sleep(0.002)
                            continue
                        t = msg.type
                        ch = getattr(msg, "channel", 0)

                        # 弯音：丢弃（鬼畜源头），点亮电平 + 终端行
                        if t == "pitchwheel":
                            self.line(f"[被过滤] pitchwheel ch{ch} {msg.pitch}",
                                      {"pb": msg.pitch})
                            continue

                        # 白名单：其它一切类型（时钟/程序变更等）用不上，丢弃
                        if t not in ("note_on", "note_off", "control_change"):
                            continue

                        # CC 分流：只留 CC1/CC64；CC7 丢但显示；其余 CC 全丢
                        if t == "control_change":
                            c = msg.control
                            name = F.CC_NAMES.get(c, f"CC{c}")
                            if c == 7:
                                self.line(f"[被过滤] CC {c:>3} ({name}) = {msg.value}",
                                          {"cc7": msg.value})
                                continue
                            if c == 1:
                                self.line(f"CC {c:>3} ({name}) = {msg.value}",
                                          {"cc1": msg.value})
                            elif c == 64:
                                self.line(f"CC {c:>3} ({name}) = {msg.value}",
                                          {"cc64": 1 if msg.value >= 64 else 0})
                            else:
                                continue

                        # 击键：转发 + 显示力度（透明观测，无整形 → raw == raw）
                        if t == "note_on" and msg.velocity > 0:
                            v = msg.velocity
                            self.line(f"[力度] vel {v} -> {v} {F.vel_bar(v)}",
                                      {"note": v})
                        elif t == "note_off" or (t == "note_on" and msg.velocity == 0):
                            self.emit({"note": 0})
                            if t == "note_off":
                                self.line(f"note_off ch{ch} n{msg.note}")

                        outp.send(msg)
            except Exception as e:
                self.line(f"错误: {e}，2 秒后重试")
                time.sleep(2)


# ================= SSE 服务（推流给浏览器） =================
class Handler(BaseHTTPRequestHandler):
    q = None
    html = ""

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = self.html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    ev = self.q.get()
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except Exception:
                pass  # 浏览器断开
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


def main():
    q = queue.Queue()
    Engine(q).start()
    Handler.q = q
    if os.path.exists(HTML_FILE):
        with open(HTML_FILE, "r", encoding="utf-8") as f:
            Handler.html = f.read()
    else:
        Handler.html = "<h1>webui.html 缺失</h1>"

    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://{HOST}:{PORT}"
    print(f"[{time.strftime('%H:%M:%S')}] Web 监控面板已启动: {url}")
    print("按 Ctrl+C 退出")
    try:
        webbrowser.open(url)
    except Exception:
        print(f"请手动打开浏览器访问: {url}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()
        print("已退出")


if __name__ == "__main__":
    main()
