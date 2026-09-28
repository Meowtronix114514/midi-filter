"""Use the computer keyboard as a small MIDI keyboard on Windows."""

import ctypes
import os
import queue
import threading
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox, ttk

import mido


if os.name == "nt":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _LRESULT = ctypes.c_ssize_t
    _WPARAM = ctypes.c_size_t
    _LPARAM = ctypes.c_ssize_t

    class _Point(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class _Message(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", _WPARAM),
            ("lParam", _LPARAM),
            ("time", wintypes.DWORD),
            ("pt", _Point),
        ]

    class _KeyboardData(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    _HookProc = ctypes.WINFUNCTYPE(
        _LRESULT, ctypes.c_int, _WPARAM, _LPARAM
    )

    _user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, _HookProc, ctypes.c_void_p, wintypes.DWORD
    ]
    _user32.SetWindowsHookExW.restype = ctypes.c_void_p
    _user32.CallNextHookEx.argtypes = [
        ctypes.c_void_p, ctypes.c_int, _WPARAM, _LPARAM
    ]
    _user32.CallNextHookEx.restype = _LRESULT
    _user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    _user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(_Message), wintypes.HWND, wintypes.UINT, wintypes.UINT
    ]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.PeekMessageW.argtypes = [
        ctypes.POINTER(_Message), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
    ]
    _user32.PeekMessageW.restype = wintypes.BOOL
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(_Message)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(_Message)]
    _user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, _WPARAM, _LPARAM
    ]
    _user32.PostThreadMessageW.restype = wintypes.BOOL
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD


WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

VK_SHIFT_KEYS = {0x10, 0xA0, 0xA1}
VK_OEM_MINUS = 0xBD
VK_OEM_PLUS = 0xBB
MIN_OCTAVE_SHIFT = -5
MAX_OCTAVE_SHIFT = 4

# A..' are the white keys C4 through F5. The upper keyboard row supplies sharps.
NOTE_KEYS = {
    0x41: 60,  # A: C4
    0x57: 61,  # W: C#4
    0x53: 62,  # S: D4
    0x45: 63,  # E: D#4
    0x44: 64,  # D: E4
    0x46: 65,  # F: F4
    0x54: 66,  # T: F#4
    0x47: 67,  # G: G4
    0x59: 68,  # Y: G#4
    0x48: 69,  # H: A4
    0x55: 70,  # U: A#4
    0x4A: 71,  # J: B4
    0x4B: 72,  # K: C5
    0x4F: 73,  # O: C#5
    0x4C: 74,  # L: D5
    0x50: 75,  # P: D#5
    0xBA: 76,  # ;: E5
    0xDE: 77,  # ': F5
    0xDD: 78,  # ]: F#5
}

class MidiOutput:
    """Own the RtMidi output port on a worker thread."""

    def __init__(self):
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._connected = False
        self._status = "未连接"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def connect(self, name):
        self._queue.put(("connect", name))

    def disconnect(self):
        self._queue.put(("disconnect", None))

    def send(self, message_type, note=None, velocity=0):
        self._queue.put((message_type, note, velocity))

    def snapshot(self):
        with self._lock:
            return self._connected, self._status

    def _set_status(self, connected, status):
        with self._lock:
            self._connected = connected
            self._status = status

    def _run(self):
        port = None
        while True:
            item = self._queue.get()
            op = item[0]

            if op == "stop":
                if port is not None:
                    try:
                        port.send(mido.Message("control_change", channel=0, control=123, value=0))
                    except Exception:
                        pass
                    try:
                        port.close()
                    except Exception:
                        pass
                self._set_status(False, "已关闭")
                return

            if op == "connect":
                if port is not None:
                    try:
                        port.close()
                    except Exception:
                        pass
                    port = None
                self._set_status(False, f"正在连接：{item[1]}")
                try:
                    port = mido.open_output(item[1])
                    port.send(mido.Message("control_change", channel=0, control=123, value=0))
                    self._set_status(True, f"已连接：{item[1]}")
                except Exception as exc:
                    if port is not None:
                        try:
                            port.close()
                        except Exception:
                            pass
                    port = None
                    self._set_status(False, f"连接失败：{exc}")
                continue

            if op == "disconnect":
                if port is not None:
                    try:
                        port.send(mido.Message("control_change", channel=0, control=123, value=0))
                        port.close()
                    except Exception:
                        pass
                    port = None
                self._set_status(False, "未连接")
                continue

            if port is None:
                continue

            try:
                if op == "note_on":
                    port.send(mido.Message("note_on", channel=0, note=item[1], velocity=item[2]))
                elif op == "note_off":
                    port.send(mido.Message("note_off", channel=0, note=item[1], velocity=0))
            except Exception as exc:
                try:
                    port.close()
                except Exception:
                    pass
                port = None
                self._set_status(False, f"输出端口异常：{exc}")

    def close(self):
        self._queue.put(("stop", None))
        self._thread.join(timeout=2)


class KeyboardMidiController:
    def __init__(self, midi_output):
        self.midi_output = midi_output
        self._lock = threading.RLock()
        self.mode = False
        self.octaves = 0
        self._shift_keys = set()
        self._shift_pending = False
        self._shift_combo = False
        self._suppressed_keys = set()
        self._held_notes = {}

    def handle_key(self, vk_code, is_down):
        with self._lock:
            if vk_code in VK_SHIFT_KEYS:
                self._handle_shift(vk_code, is_down)
                return False  # Preserve normal Shift shortcuts, including Shift+=.

            if is_down and self._shift_keys:
                self._shift_combo = True

            if not is_down and vk_code in self._suppressed_keys:
                self._suppressed_keys.discard(vk_code)
                note = self._held_notes.pop(vk_code, None)
                if note is not None:
                    self.midi_output.send("note_off", note)
                return True

            if is_down and vk_code in self._suppressed_keys:
                return True  # Ignore Windows key-repeat for held notes and controls.

            if not self.mode:
                return False

            if is_down and vk_code in NOTE_KEYS:
                note = max(0, min(127, NOTE_KEYS[vk_code] + self.octaves * 12))
                self._suppressed_keys.add(vk_code)
                self._held_notes[vk_code] = note
                self.midi_output.send("note_on", note, 100)
                return True

            if is_down and vk_code == VK_OEM_MINUS and not self._shift_keys:
                self.octaves = max(MIN_OCTAVE_SHIFT, self.octaves - 1)
                self._suppressed_keys.add(vk_code)
                return True

            if is_down and vk_code == VK_OEM_PLUS and self._shift_keys:
                self.octaves = min(MAX_OCTAVE_SHIFT, self.octaves + 1)
                self._suppressed_keys.add(vk_code)
                return True

            return False

    def _handle_shift(self, vk_code, is_down):
        if is_down:
            if vk_code not in self._shift_keys:
                if not self._shift_keys:
                    self._shift_pending = True
                    self._shift_combo = False
                self._shift_keys.add(vk_code)
            return

        self._shift_keys.discard(vk_code)
        if not self._shift_keys and self._shift_pending:
            toggle = not self._shift_combo
            self._shift_pending = False
            self._shift_combo = False
            if toggle:
                self.mode = not self.mode
                if not self.mode:
                    self.release_notes()

    def release_notes(self):
        with self._lock:
            for note in self._held_notes.values():
                self.midi_output.send("note_off", note)
            self._held_notes.clear()

    def snapshot(self):
        with self._lock:
            return self.mode, self.octaves


class GlobalKeyboardHook:
    def __init__(self, controller):
        self.controller = controller
        self.error = None
        self._ready = threading.Event()
        self._thread_id = None
        self._handle = None
        self._callback = _HookProc(self._hook_callback) if os.name == "nt" else None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        if os.name != "nt":
            self.error = "全局键盘映射只支持 Windows。"
            return
        self._thread.start()
        self._ready.wait(timeout=3)
        if not self._ready.is_set():
            self.error = "启动键盘监听超时。"

    def _run(self):
        self._thread_id = _kernel32.GetCurrentThreadId()
        message = _Message()
        _user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        module = _kernel32.GetModuleHandleW(None)
        self._handle = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._callback, module, 0)
        if not self._handle:
            self.error = f"安装全局键盘钩子失败（错误码 {ctypes.get_last_error()}）。"
            self._ready.set()
            return

        self._ready.set()
        while True:
            result = _user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result <= 0:
                break
            _user32.TranslateMessage(ctypes.byref(message))
            _user32.DispatchMessageW(ctypes.byref(message))

        if self._handle:
            _user32.UnhookWindowsHookEx(self._handle)
            self._handle = None

    def _hook_callback(self, code, w_param, l_param):
        if code == HC_ACTION:
            if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                is_down = True
            elif w_param in (WM_KEYUP, WM_SYSKEYUP):
                is_down = False
            else:
                is_down = None

            if is_down is not None:
                try:
                    key = ctypes.cast(l_param, ctypes.POINTER(_KeyboardData)).contents
                    if self.controller.handle_key(int(key.vkCode), is_down):
                        return 1
                except Exception:
                    pass

        return _user32.CallNextHookEx(self._handle, code, w_param, l_param)

    def stop(self):
        if self._thread_id and self._handle:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=1)


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("键盘 MIDI 模式")
        self.root.geometry("560x390")
        self.root.minsize(520, 360)

        self.output = MidiOutput()
        self.controller = KeyboardMidiController(self.output)
        self.hook = GlobalKeyboardHook(self.controller)

        self.port_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="MIDI 模式：关闭（单独按 Shift 开启）")
        self.transpose_var = tk.StringVar(value="八度移调：+0")
        self.connection_var = tk.StringVar(value="未连接 MIDI 输出")

        self._build_ui()
        self.refresh_ports()
        self.hook.start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._refresh_status)

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="电脑键盘 → MIDI", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="连接 loopMIDI 输出后，在任何窗口中用键盘弹奏。",
        ).pack(anchor="w", pady=(2, 12))

        port_row = ttk.Frame(frame)
        port_row.pack(fill="x", pady=(0, 10))
        ttk.Label(port_row, text="MIDI 输出：").pack(side="left")
        self.port_box = ttk.Combobox(port_row, textvariable=self.port_var, state="readonly")
        self.port_box.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(port_row, text="刷新", command=self.refresh_ports).pack(side="left", padx=(0, 6))
        self.connect_button = ttk.Button(port_row, text="连接", command=self.toggle_connection)
        self.connect_button.pack(side="left")

        ttk.Label(frame, textvariable=self.connection_var, foreground="#555").pack(anchor="w")
        ttk.Separator(frame).pack(fill="x", pady=12)

        self.mode_label = ttk.Label(frame, textvariable=self.mode_var, font=("Segoe UI", 12, "bold"))
        self.mode_label.pack(anchor="w")
        ttk.Label(frame, textvariable=self.transpose_var).pack(anchor="w", pady=(2, 10))

        ttk.Label(frame, text="白键：A S D F G H J K L ; '").pack(anchor="w")
        ttk.Label(frame, text="黑键：W E T Y U O P ]").pack(anchor="w")
        ttk.Label(frame, text="单独按 Shift：切换 MIDI 模式    -：降八度    Shift+=：升八度").pack(
            anchor="w", pady=(3, 0)
        )
        ttk.Label(frame, text="A 从 C4 开始；按住时发音，松开时止音；固定力度 100。", foreground="#555").pack(
            anchor="w", pady=(10, 0)
        )

    def refresh_ports(self):
        try:
            names = mido.get_output_names()
        except Exception as exc:
            names = []
            self.connection_var.set(f"读取 MIDI 端口失败：{exc}")
        self.port_box["values"] = names
        if self.port_var.get() not in names:
            preferred = next((name for name in names if "loopmidi" in name.lower()), None)
            self.port_var.set(preferred or (names[0] if names else ""))
        if not names:
            self.connection_var.set("没有可用输出；请先创建 loopMIDI 虚拟端口。")

    def toggle_connection(self):
        connected, _ = self.output.snapshot()
        if connected:
            self.controller.release_notes()
            self.output.disconnect()
            return
        name = self.port_var.get().strip()
        if not name:
            messagebox.showwarning("选择 MIDI 输出", "请先选择一个 MIDI 输出端口。")
            return
        self.controller.release_notes()
        self.output.connect(name)

    def _refresh_status(self):
        connected, connection_text = self.output.snapshot()
        mode, octaves = self.controller.snapshot()
        self.connection_var.set(connection_text)
        self.connect_button.configure(text="断开" if connected else "连接")
        self.connect_button.configure(state="disabled" if connection_text.startswith("正在连接：") else "normal")
        if self.hook.error:
            self.mode_var.set(f"键盘监听不可用：{self.hook.error}")
            self.mode_label.configure(foreground="#a00")
        elif mode and connected:
            self.mode_var.set("MIDI 模式：已开启（单独按 Shift 关闭）")
            self.mode_label.configure(foreground="#080")
        elif mode:
            self.mode_var.set("MIDI 模式：已开启，但 MIDI 输出未连接")
            self.mode_label.configure(foreground="#a60")
        else:
            self.mode_var.set("MIDI 模式：关闭（单独按 Shift 开启）")
            self.mode_label.configure(foreground="#555")
        self.transpose_var.set(f"八度移调：{octaves:+d}")
        self.root.after(100, self._refresh_status)

    def close(self):
        self.controller.release_notes()
        self.hook.stop()
        self.output.close()
        self.root.destroy()


def main():
    if os.name != "nt":
        raise SystemExit("键盘 MIDI 模式当前只支持 Windows。")
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
