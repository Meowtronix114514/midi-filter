#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MIDI CC 监控台 (桌面端 tkinter) - v1
=====================================================
链路: 键盘 ──USB──▶ 本程序 ──▶ loopMIDI 虚拟端口 ──▶ DAW

功能:
  1. 实时监控 CC：CC 号 / 名称 / 当前值 / 条形图 / 计数 / 最后时间
  2. 监控列表运行中动态增删，**立即生效，无需重启**
  3. 手动调整 CC：滑块 + 数字输入，可单发，可"持续发送"
  4. 可选转发所有消息到 loopMIDI（可同时过滤弯音），
     运行时顶替 midi_pitch_filter.py 在链路中的位置

用法:
    python cc_monitor_gui.py          # 打开桌面窗口
    双击 cc_monitor.bat 也可以
"""
import json
import os
import time
import datetime

import tkinter as tk
from tkinter import ttk, messagebox

import mido

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "cc_monitor_config.json")

REFRESH_MS = 40          # 界面刷新间隔
POLL_SLEEP_MS = 1        # 输入轮询间隔（与 after 合并使用）
HEALTH_INTERVAL = 2.0    # 端口健康检查间隔（秒）
BARS = 16                # 条形图格数

# 常见 CC 名称（未列出的显示 CC#N）
CC_NAMES = {
    0: "Bank Select", 1: "Mod Wheel", 5: "Portamento", 6: "Data Entry",
    7: "Volume", 8: "Balance", 10: "Pan", 11: "Expression",
    64: "Sustain Pedal", 65: "Portamento On/Off", 66: "Sostenuto",
    67: "Soft Pedal", 68: "Legato", 71: "Resonance", 72: "Release",
    73: "Attack", 74: "Cutoff", 91: "Reverb", 92: "Tremolo",
    93: "Chorus", 94: "Celeste", 95: "Phaser", 120: "All Sound Off",
    121: "Reset All Ctrl", 123: "All Notes Off",
}


def cc_name(n):
    return CC_NAMES.get(n, f"CC#{n}")


def bar(v):
    filled = round(BARS * v / 127)
    return "█" * filled + "░" * (BARS - filled)


def ts():
    return datetime.datetime.now().strftime("%H:%M:%S")


class App:
    def __init__(self, root):
        self.root = root
        root.title("MIDI CC 监控台")
        root.geometry("860x680")

        self.inport = None
        self.outport = None
        self.in_name = None
        self.out_name = None

        # 监控列表: {cc_number: {"value": int, "count": int, "last": str}}
        self.watch = {}
        self.counts = {}          # 所有出现过的 cc -> 累计次数（监控外的也统计）

        self.forward = tk.BooleanVar(value=True)
        self.drop_pitch = tk.BooleanVar(value=True)
        self.block_cc7 = tk.BooleanVar(value=False)
        self.continuous = tk.BooleanVar(value=False)
        self._cont_job = None
        self._last_health = time.time()
        self._running = True

        self._build()
        self._load_config()
        self._refresh_ports()
        self._poll()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ================= 界面 =================
    def _build(self):
        frm = ttk.Frame(self.root, padding=8)
        frm.pack(fill="both", expand=True)

        # ---- 端口区 ----
        pf = ttk.LabelFrame(frm, text="端口", padding=6)
        pf.pack(fill="x")
        ttk.Label(pf, text="输入:").grid(row=0, column=0, sticky="w")
        self.in_cb = ttk.Combobox(pf, width=32, state="readonly")
        self.in_cb.grid(row=0, column=1, padx=4)
        ttk.Label(pf, text="输出:").grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.out_cb = ttk.Combobox(pf, width=32, state="readonly")
        self.out_cb.grid(row=0, column=3, padx=4)
        ttk.Button(pf, text="刷新端口", command=self._refresh_ports).grid(row=0, column=4, padx=6)
        self.conn_btn = ttk.Button(pf, text="连接", command=self.toggle_connect)
        self.conn_btn.grid(row=0, column=5)
        self.port_status = ttk.Label(pf, text="未连接", foreground="#a00")
        self.port_status.grid(row=1, column=0, columnspan=6, sticky="w", pady=(4, 0))

        # ---- 转发开关 ----
        ff = ttk.Frame(pf)
        ff.grid(row=2, column=0, columnspan=6, sticky="w", pady=(4, 0))
        ttk.Checkbutton(ff, text="转发所有消息到输出", variable=self.forward,
                        command=self._save_config).pack(side="left")
        ttk.Checkbutton(ff, text="过滤弯音(Pitch Bend)", variable=self.drop_pitch,
                        command=self._save_config).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(ff, text="过滤 CC7（主音量）", variable=self.block_cc7,
                        command=self._save_config).pack(side="left", padx=(12, 0))

        # ---- 监控表 ----
        mf = ttk.LabelFrame(frm, text="CC 监控（实时）", padding=6)
        mf.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("cc", "name", "value", "bar", "count", "last")
        # 电平条用等宽字体渲染，保证 16 格长度恒定、█/░ 对齐（否则条长看着不固定）
        style = ttk.Style(self.root)
        style.configure("Meter.Treeview", font=("Consolas", 10), rowheight=24)
        style.configure("Meter.Treeview.Heading", font=("Microsoft YaHei UI", 9))
        self.tree = ttk.Treeview(mf, columns=cols, show="headings", height=10,
                                 style="Meter.Treeview")
        for cid, text, w, anchor in [
            ("cc", "CC", 50, "center"),
            ("name", "名称", 130, "w"),
            ("value", "当前值", 60, "center"),
            ("bar", "电平", 230, "w"),
            ("count", "计数", 70, "center"),
            ("last", "最后时间", 80, "center"),
        ]:
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=w, anchor=anchor)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mf, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.config(yscrollcommand=sb.set)
        self.tree.tag_configure("active", background="#eef7ff")

        # ---- 监控列表管理 ----
        af = ttk.Frame(frm)
        af.pack(fill="x", pady=4)
        ttk.Label(af, text="添加 CC:").pack(side="left")
        self.add_var = tk.StringVar()
        ttk.Entry(af, textvariable=self.add_var, width=6).pack(side="left", padx=4)
        ttk.Button(af, text="添加到监控", command=self.add_watch).pack(side="left")
        ttk.Button(af, text="移除选中", command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(af, text="全部移除", command=self.clear_watch).pack(side="left")
        self.hint = ttk.Label(af, text="运行中添加/移除立即生效，无需重启", foreground="#777")
        self.hint.pack(side="left", padx=10)

        # ---- 手动发送 ----
        sf = ttk.LabelFrame(frm, text="手动调整 CC", padding=6)
        sf.pack(fill="x", pady=(4, 0))
        row1 = ttk.Frame(sf); row1.pack(fill="x")
        ttk.Label(row1, text="CC:").pack(side="left")
        self.send_cc = tk.StringVar()
        self.send_cb = ttk.Combobox(row1, textvariable=self.send_cc, width=8,
                                    values=[str(i) for i in range(128)])
        self.send_cb.pack(side="left", padx=4)
        ttk.Label(row1, text="值:").pack(side="left", padx=(10, 0))
        self.send_val = tk.IntVar(value=64)
        self.scale = ttk.Scale(row1, from_=0, to=127, variable=self.send_val,
                               orient="horizontal", length=260,
                               command=self._on_scale)
        self.scale.pack(side="left", padx=6)
        self.val_lbl = ttk.Label(row1, text="64", width=4, font=("Consolas", 11))
        self.val_lbl.pack(side="left")
        ttk.Button(row1, text="发送一次", command=self.send_once).pack(side="left", padx=8)
        ttk.Checkbutton(row1, text="持续发送(每200ms)", variable=self.continuous,
                        command=self._toggle_continuous).pack(side="left")

        # ---- 日志 ----
        lf = ttk.LabelFrame(frm, text="日志", padding=4)
        lf.pack(fill="x", pady=(8, 0))
        self.log_box = tk.Text(lf, height=6, font=("Consolas", 9), state="disabled")
        self.log_box.pack(fill="x")

    # ================= 端口 =================
    def _refresh_ports(self):
        try:
            ins = mido.get_input_names()
        except Exception:
            ins = []
        try:
            outs = mido.get_output_names()
        except Exception:
            outs = []
        self.in_cb["values"] = ins
        self.out_cb["values"] = outs
        if ins:
            # 默认选非 loopMIDI 的（键盘）
            self.in_cb.set(next((n for n in ins if "loopMIDI" not in n), ins[0]))
        if outs:
            self.out_cb.set(next((n for n in outs if "loopMIDI" in n), outs[0]))

    def toggle_connect(self):
        if self.inport is not None or self.outport is not None:
            self._disconnect()
            return
        in_name = self.in_cb.get().strip()
        out_name = self.out_cb.get().strip()
        if not in_name or not out_name:
            messagebox.showwarning("提示", "请先选择输入/输出端口。")
            return
        try:
            self.inport = mido.open_input(in_name)
            try:
                self.outport = mido.open_output(out_name)
            except Exception as e:
                self.inport.close()
                self.inport = None
                raise e
            self.in_name, self.out_name = in_name, out_name
            self.port_status.config(text=f"已连接: {in_name}  →  {out_name}", foreground="#080")
            self.conn_btn.config(text="断开")
            self.log(f"已连接 {in_name} -> {out_name}")
        except Exception as e:
            self.port_status.config(text=f"连接失败: {e}", foreground="#a00")
            self.log(f"连接失败: {e}")

    def _disconnect(self):
        for p in (self.inport, self.outport):
            try:
                if p is not None:
                    p.close()
            except Exception:
                pass
        self.inport = self.outport = None
        self.port_status.config(text="未连接", foreground="#a00")
        self.conn_btn.config(text="连接")
        self.log("已断开")

    # ================= 输入轮询 =================
    def _poll(self):
        if not self._running:
            return
        if self.inport is not None:
            try:
                for msg in self.inport.iter_pending():
                    self._handle(msg)
            except Exception:
                self.log("输入端口异常，已断开（可重连）")
                self._disconnect()
        # 健康检查
        if self.inport is not None:
            now = time.time()
            if now - self._last_health >= HEALTH_INTERVAL:
                self._last_health = now
                try:
                    names = mido.get_input_names()
                    if self.in_name not in names:
                        raise RuntimeError("input gone")
                except Exception:
                    self.log("检测到设备断开，自动断开。重插后点\"刷新端口\"+\"连接\"")
                    self._disconnect()
        self.root.after(REFRESH_MS, self._poll)

    def _handle(self, msg):
        if msg.type == "control_change":
            cc, val = msg.control, msg.value
            self.counts[cc] = self.counts.get(cc, 0) + 1
            if cc in self.watch:
                self.watch[cc].update(value=val, count=self.watch[cc].get("count", 0) + 1,
                                      last=ts())
                self.log(f"CC {cc} ({cc_name(cc)}) -> {val} {bar(val)}")
        if self.forward.get():
            if self.drop_pitch.get() and msg.type == "pitchwheel":
                return
            if self.block_cc7.get() and msg.type == "control_change" and msg.control == 7:
                return
            try:
                if self.outport is not None:
                    self.outport.send(msg)
            except Exception:
                self.log("输出端口异常，已断开")
                self._disconnect()

    # ================= 监控列表 =================
    def add_watch(self):
        txt = self.add_var.get().strip()
        if not txt:
            return
        try:
            cc = int(txt)
            assert 0 <= cc <= 127
        except (ValueError, AssertionError):
            messagebox.showerror("错误", "CC 必须是 0~127 的整数。")
            return
        if cc in self.watch:
            self.log(f"CC {cc} 已在监控中")
        else:
            self.watch[cc] = {"value": 0, "count": 0, "last": "-"}
            self.log(f"已添加 CC {cc} ({cc_name(cc)}) 到监控")
        self.add_var.set("")
        self._sync_tree()
        self._save_config()

    def remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        for iid in sel:
            cc = int(self.tree.item(iid, "values")[0])
            self.watch.pop(cc, None)
            self.log(f"已移除 CC {cc}")
        self._sync_tree()
        self._save_config()

    def clear_watch(self):
        self.watch.clear()
        self.log("已清空监控列表")
        self._sync_tree()
        self._save_config()

    def _sync_tree(self):
        """按当前 watch 列表重建行（保留已有值）。"""
        existing = {int(self.tree.item(i, "values")[0]) for i in self.tree.get_children()}
        for cc in list(self.watch.keys()):
            if cc in existing:
                continue
            d = self.watch[cc]
            self.tree.insert("", "end", iid=str(cc),
                             values=(cc, cc_name(cc), d["value"], bar(d["value"]),
                                     d["count"], d["last"]))
        for iid in list(self.tree.get_children()):
            if int(iid) not in self.watch:
                self.tree.delete(iid)

    def _update_tree_values(self):
        for cc, d in self.watch.items():
            iid = str(cc)
            if self.tree.exists(iid):
                self.tree.item(iid, values=(cc, cc_name(cc), d["value"],
                                            bar(d["value"]), d["count"], d["last"]))

    # ================= 手动发送 =================
    def _on_scale(self, _v):
        self.val_lbl.config(text=str(self.send_val.get()))

    def _touch_watch(self, cc, val):
        """手动操作时同步监控列表：不在监控中的 CC 自动加入，条立刻可见。"""
        if cc in self.watch:
            self.watch[cc].update(value=val, count=self.watch[cc].get("count", 0) + 1,
                                  last=ts())
        else:
            self.watch[cc] = {"value": val, "count": 1, "last": ts()}
            self._sync_tree()
            self._save_config()
            self.log(f"已自动添加 CC {cc} ({cc_name(cc)}) 到监控")

    def send_once(self):
        try:
            cc = int(self.send_cc.get())
            assert 0 <= cc <= 127
        except (ValueError, AssertionError):
            messagebox.showerror("错误", "请先选择要发送的 CC 号(0~127)。")
            return
        val = max(0, min(127, int(self.send_val.get())))
        try:
            if self.outport is None:
                messagebox.showwarning("提示", "尚未连接输出端口。")
                return
            self.outport.send(mido.Message("control_change", control=cc, value=val))
            self.log(f"[手动] CC {cc} ({cc_name(cc)}) = {val}")
            self._touch_watch(cc, val)
        except Exception as e:
            messagebox.showerror("错误", f"发送失败: {e}")

    def _toggle_continuous(self):
        if self._cont_job is not None:
            self.root.after_cancel(self._cont_job)
            self._cont_job = None
        if self.continuous.get():
            self._cont_loop()

    def _cont_loop(self):
        if not self.continuous.get():
            return
        try:
            cc = int(self.send_cc.get())
            val = max(0, min(127, int(self.send_val.get())))
            if self.outport is not None:
                self.outport.send(mido.Message("control_change", control=cc, value=val))
                self._touch_watch(cc, val)
        except (ValueError, AssertionError):
            pass
        except Exception:
            pass
        self._cont_job = self.root.after(200, self._cont_loop)

    # ================= 日志/配置 =================
    def log(self, text):
        self.log_box.config(state="normal")
        self.log_box.insert("end", f"[{ts()}] {text}\n")
        # 只保留最近 200 行
        if int(self.log_box.index("end-1c").split(".")[0]) > 200:
            self.log_box.delete("1.0", "2.0")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _save_config(self):
        try:
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump({
                    "watch": sorted(self.watch.keys()),
                    "forward": self.forward.get(),
                    "drop_pitch": self.drop_pitch.get(),
                    "block_cc7": self.block_cc7.get(),
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_config(self):
        d = {}
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            pass
        watch = d.get("watch")
        if not watch:
            # 默认监控：CC1 调制轮 / CC7 主音量 / CC64 延音踏板
            watch = [1, 7, 64]
        for cc in watch:
            self.watch[int(cc)] = {"value": 0, "count": 0, "last": "-"}
        self.forward.set(d.get("forward", True))
        self.drop_pitch.set(d.get("drop_pitch", True))
        self.block_cc7.set(d.get("block_cc7", False))
        self._sync_tree()

    def on_close(self):
        self._running = False
        self._save_config()
        self._disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = App(root)
    # 定时刷新表格数值
    def tick():
        if app._running:
            app._update_tree_values()
            root.after(REFRESH_MS, tick)
    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
