#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MIDI 弯音过滤器 + 力度整形 (Windows) - v6.2
=====================================================
把键盘输入的 MIDI 做如下处理，再转发到虚拟端口：
  1. 过滤 Pitch Bend（弯音）消息（默认启用）
  2. 力度整形：
     - 线性缩放（--vel-scale）
     - 幂函数曲线（--vel-power）
     - 分段线性曲线（--curve "raw:target,raw:target,..."，优先级最高，
       可精确模拟钢琴手感）
  3. 丢弃指定 CC 控制器（--block-cc，如 "7" 禁用主音量，可逗号分隔多个）
其余消息（CC、时钟等）默认原样透传。

特性：
  * --all-log：实时打印所有 MIDI 信号（note/CC/弯音/时钟等），被过滤的标 [被过滤]
  * 端口打开失败自动重试（原子操作，杜绝句柄泄漏）
  * 键盘断开/重插自动恢复（健康检查 + 无限重连）
  * 重插后端口名变化自动重新匹配
  * 单实例互斥锁

架构: 键盘 ──USB──▶ 本程序 ──▶ loopMIDI 虚拟端口 ──▶ DAW

用法:
    python midi_pitch_filter.py --list
    python midi_pitch_filter.py --auto
    python midi_pitch_filter.py [输入端口名 输出端口名]
    python midi_pitch_filter.py --vel-scale 0.5 --vel-log 20
    python midi_pitch_filter.py --curve "50:35,110:80,127:127" --vel-log 40
    python midi_pitch_filter.py --auto --curve "..." --stuck-notes "60" --stuck-timeout 0.3
    python midi_pitch_filter.py --auto --curve "..." --block-cc 7 --all-log
"""

import sys
import time
import datetime
import argparse
import mido

HEALTH_INTERVAL = 1.0    # 健康检查间隔（秒）
RECONNECT_DELAY = 2.0    # 重连等待间隔（秒）
STUCK_CHECK_INTERVAL = 0.2  # 卡音看门狗检查间隔（秒）
VEL_MIN = 1
VEL_MAX = 127

CC_NAMES = {
    1: "调制轮 ModWheel", 2: "呼吸 Breath", 4: "踏板1", 5: "踏板2",
    7: "主音量 Volume", 8: "平衡 Balance", 10: "声像 Pan",
    11: "表情 Expression", 64: "延音踏板 Sustain", 66: "持续音 Sostenuto",
    67: "弱音 Soft", 71: "泛音共振", 72: "释音", 73: "起音", 74: "亮度",
    91: "混响 Reverb", 92: "颤音 Tremolo", 93: "合唱 Chorus",
    94: "增音 Celeste", 95: "相位 Phaser",
}

_MUTEX_HANDLE = None


def ts():
    """当前时间戳，用于日志。"""
    return datetime.datetime.now().strftime("%H:%M:%S")


def ensure_single_instance(name):
    """Windows 命名互斥量：防止过滤器/哨兵重复运行。"""
    global _MUTEX_HANDLE
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, name)
        err = ctypes.get_last_error()
        if err == 183:  # ERROR_ALREADY_EXISTS
            return False
    except Exception:
        pass
    return True


def parse_args():
    """解析命令行参数。"""
    p = argparse.ArgumentParser(description="MIDI 弯音过滤器 + 力度整形")
    p.add_argument("ports", nargs="*", help="[输入端口名 输出端口名]，不填则交互式选择")
    p.add_argument("--list", action="store_true", help="列出所有 MIDI 端口")
    p.add_argument("--auto", action="store_true", help="自动识别端口（供哨兵调用）")
    p.add_argument("--vel-scale", type=float, default=1.0,
                   help="力度线性缩放系数（默认 1.0 不处理）")
    p.add_argument("--vel-power", type=float, default=1.0,
                   help="力度幂函数曲线指数（默认 1.0 直线）")
    p.add_argument("--curve", default="",
                   help="分段线性力度曲线，格式 \"raw:target,raw:target,...\"，"
                        "如 \"50:35,110:80,127:127\"。设置后优先于 scale/power。")
    p.add_argument("--vel-log", type=int, default=0,
                   help="力度日志：N=只打印前N个；-1=实时无限打印；0=不打印")
    p.add_argument("--stuck-notes", default="",
                   help="卡音看门狗：要监控的 MIDI note 编号，逗号分隔，"
                        "如 \"60\" 或 \"60,64\"。只有这些音超时无 note-off 才强制释放。")
    p.add_argument("--stuck-timeout", type=float, default=0.3,
                   help="卡音超时（秒）：指定音符超过该时间仍无 note-off 会被强制释放（默认 0.3）")
    p.add_argument("--stuck-minvel", type=int, default=127,
                   help="卡音触发力度下限：只有原始力度 >= 该值的敲击才启用看门狗（默认 127，"
                        "对应\"硬击到 127 才卡\"的硬件特征）")
    p.add_argument("--block-cc", default="",
                   help="要丢弃的 CC(控制器)编号，逗号分隔，如 \"7\" 禁用主音量(CC7)，"
                        "\"7,9\" 同时丢弃 7 和 9")
    p.add_argument("--all-log", action="store_true",
                   help="实时打印所有 MIDI 信号（note/CC/弯音/时钟等），被过滤的标 [被过滤]")
    p.add_argument("--show-cc", default="",
                   help="全量输出时额外显示这些 CC 编号（逗号分隔），如 \"1,10,64\"；"
                        "未列出的 CC 不显示；被过滤的 CC 无论是否列出都始终显示")
    return p.parse_args()


def parse_curve(spec):
    """解析 \"50:35,110:80,127:127\" 为 [(50,35),(110,80),(127,127)]。"""
    pts = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        a, b = part.split(":")
        pts.append((int(a.strip()), int(b.strip())))
    if not pts:
        return []
    pts.sort(key=lambda p: p[0])
    # 首尾补边界
    if pts[0][0] > 0:
        pts.insert(0, (0, pts[0][1]))
    if pts[-1][0] < 127:
        pts.append((127, pts[-1][1]))
    return pts


def parse_stuck_notes(spec):
    """解析 \"60\" 或 \"60,64\" 为 {60,64}，用于卡音看门狗指定音符。"""
    notes = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            notes.add(int(part))
        except ValueError:
            pass
    return notes


def parse_cc_set(spec):
    """解析 \"7\" 或 \"7,9\" 为 {7,9}，表示要丢弃的控制器编号（0-127）。"""
    ccs = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
            if 0 <= n <= 127:
                ccs.add(n)
        except ValueError:
            pass
    return ccs


def curve_velocity(v, pts):
    """分段线性插值。v<=0 由调用方处理（止音消息不过来）。"""
    if v <= pts[0][0]:
        return pts[0][1]
    if v >= pts[-1][0]:
        return pts[-1][1]
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= v <= x1:
            if x1 == x0:
                return y1
            return int(round(y0 + (y1 - y0) * (v - x0) / (x1 - x0)))
    return v


def transform_velocity(v, scale, power, curve_pts):
    """力度整形入口。v==0 的止音消息由调用方直接放行，不进这里。"""
    if curve_pts:
        return max(VEL_MIN, min(VEL_MAX, curve_velocity(v, curve_pts)))
    if power != 1.0:
        v = 127.0 * ((v / 127.0) ** power)
    v = v * scale
    v = int(v + 0.5)
    return max(VEL_MIN, min(VEL_MAX, v))


def vel_bar(v):
    """把力度画成 16 格条形图，便于实时观察轻重。"""
    filled = round(16 * v / 127)
    return "█" * filled + "░" * (16 - filled)


def msg_summary(msg):
    """把一条消息压缩成一行可读文字，用于 --all-log 全量输出。"""
    t = msg.type
    if t == "note_on":
        return f"note_on  ch{msg.channel} n{msg.note} vel{msg.velocity}"
    if t == "note_off":
        return f"note_off ch{msg.channel} n{msg.note}"
    if t == "control_change":
        name = CC_NAMES.get(msg.control, f"CC{msg.control}")
        return f"CC{msg.control:>3} ({name}) = {msg.value}"
    if t == "pitchwheel":
        return f"pitchwheel ch{msg.channel} {msg.pitch}"
    if t == "program_change":
        return f"program ch{msg.channel} {msg.program}"
    if t == "polytouch":
        return f"polytouch ch{msg.channel} n{msg.note} {msg.value}"
    if t == "aftertouch":
        return f"aftertouch ch{msg.channel} {msg.value}"
    return f"{t} {dict(msg)}"


def safe_input_names():
    try:
        return mido.get_input_names()
    except Exception:
        return []


def safe_output_names():
    try:
        return mido.get_output_names()
    except Exception:
        return []


def resolve_in_name(preferred, auto_mode):
    """当前可用的输入端口名：自动模式重新识别；手动模式找不到时按特征匹配。"""
    ins = safe_input_names()
    if auto_mode:
        return next((n for n in ins if "loopMIDI" not in n), None)
    if preferred in ins:
        return preferred
    cand = next((n for n in ins if "Keystation" in n), None)
    if cand is None:
        cand = next((n for n in ins if "loopMIDI" not in n), None)
    if cand and cand != preferred:
        print(f"[{ts()}]  输入端口名已变化: '{preferred}' -> '{cand}'")
    return cand


def resolve_out_name(preferred, auto_mode):
    """当前可用的输出端口名（loopMIDI）。"""
    outs = safe_output_names()
    if auto_mode:
        return next((n for n in outs if "loopMIDI" in n), None)
    if preferred in outs:
        return preferred
    cand = next((n for n in outs if "loopMIDI" in n), None)
    if cand and cand != preferred:
        print(f"[{ts()}]  输出端口名已变化: '{preferred}' -> '{cand}'")
    return cand


def list_ports():
    """列出所有 MIDI 输入/输出端口。"""
    ins = safe_input_names()
    outs = safe_output_names()
    print("=== MIDI 输入端口 (In) ===")
    for i, n in enumerate(ins):
        print(f"  [{i}] {n}")
    print("=== MIDI 输出端口 (Out) ===")
    for i, n in enumerate(outs):
        print(f"  [{i}] {n}")
    return ins, outs


def pick(names, prompt):
    """交互式选择一个端口。"""
    if not names:
        print("没有可用端口！请检查设备连接。")
        sys.exit(1)
    while True:
        try:
            idx = int(input(prompt))
            return names[idx]
        except (ValueError, IndexError):
            print("编号无效，请重新输入。")


def ports_healthy(in_name, out_name):
    """检查指定端口是否仍然存在。"""
    try:
        return (in_name in safe_input_names()
                and out_name in safe_output_names())
    except Exception:
        return False


def open_ports(in_name, out_name, auto_mode):
    """打开输入+输出端口（原子操作，杜绝句柄泄漏）。"""
    for attempt in range(1, 6):
        cur_in = resolve_in_name(in_name, auto_mode)
        cur_out = resolve_out_name(out_name, auto_mode)
        if not cur_in or not cur_out:
            print(f"[{ts()}]  端口尚未就绪(第{attempt}次)，等待设备出现...")
            time.sleep(2)
            continue

        inport = outport = None
        try:
            inport = mido.open_input(cur_in)
        except Exception as e:
            print(f"[{ts()}]  打开输入端口失败(第{attempt}次): {e}")
            time.sleep(2)
            continue
        try:
            outport = mido.open_output(cur_out)
        except Exception as e:
            print(f"[{ts()}]  打开输出端口失败(第{attempt}次): {e}，关闭已打开的输入口...")
            try:
                inport.close()
            except Exception:
                pass
            time.sleep(2)
            continue
        return inport, outport, cur_in, cur_out
    return None, None, None, None


def run(in_name, out_name, auto_mode, vel_scale, vel_power, curve_pts, vel_log,
        stuck_notes, stuck_timeout, stuck_minvel, block_cc, all_log, show_cc):
    """主循环：连接 -> 处理转发 -> 断线自动重连，无限循环。"""
    vel_enabled = (vel_scale != 1.0) or (vel_power != 1.0) or bool(curve_pts)
    if vel_enabled:
        if curve_pts:
            print(f"[{ts()}] 力度曲线开启: {curve_pts}")
        else:
            print(f"[{ts()}] 力度整形开启: scale={vel_scale}, power={vel_power}")
    if stuck_notes:
        print(f"[{ts()}] 卡音看门狗开启: 监控音符={sorted(stuck_notes)} "
              f"超时={stuck_timeout}s 触发力度>={stuck_minvel}")
    if block_cc:
        print(f"[{ts()}] 禁用 CC: {sorted(block_cc)}（这些控制消息将被丢弃）")
    if all_log:
        print(f"[{ts()}] 全量信号输出开启：所有 MIDI 信号实时打印")

    while True:
        try:
            if auto_mode:
                in_name, out_name = resolve_in_name(in_name, auto_mode), resolve_out_name(out_name, auto_mode)
                if not in_name or not out_name:
                    print(f"[{ts()}] 暂时找不到键盘或 loopMIDI 端口，2 秒后重试...")
                    time.sleep(RECONNECT_DELAY)
                    continue

            inport, outport, cur_in, cur_out = open_ports(in_name, out_name, auto_mode)
            if inport is None or outport is None:
                print(f"[{ts()}] 端口打开失败，稍后重试...")
                time.sleep(RECONNECT_DELAY)
                continue

            dropped = 0
            dropped_cc = 0
            vel_logged = 0
            print(f"[{ts()}] 连接成功: {cur_in}  ->  {cur_out}")
            print(f"[{ts()}] 开始处理 MIDI... (Ctrl+C 退出，拔插键盘自动恢复)")

            with inport, outport:
                last_check = time.time()
                active_stuck = {}            # {(channel,note): 按下时刻}
                last_stuck_check = time.time()
                last_cc_printed = {}         # {cc: 上次打印时刻}，用于被过滤 CC 限速
                while True:
                    try:
                        msg = inport.poll()
                    except Exception:
                        print(f"[{ts()}]  输入端口异常，尝试重连...")
                        break

                    if msg is not None:
                        # 1) 过滤弯音
                        if msg.type == "pitchwheel":
                            dropped += 1
                            if all_log or dropped == 1 or dropped % 50 == 0:
                                print(f"[{ts()}]  [被过滤] pitchwheel ch{msg.channel} {msg.pitch}")
                            continue

                        # 1.5) 丢弃指定 CC（如 CC7 主音量），实时标注被过滤
                        if block_cc and msg.type == "control_change" and msg.control in block_cc:
                            dropped_cc += 1
                            _ccnow = time.time()
                            if all_log or dropped_cc == 1 or (_ccnow - last_cc_printed.get(msg.control, 0)) >= 0.5:
                                last_cc_printed[msg.control] = _ccnow
                                _ccname = CC_NAMES.get(msg.control, f"CC{msg.control}")
                                print(f"[{ts()}]  [被过滤] CC{msg.control:>3} ({_ccname}) = {msg.value}")
                            continue

                        # 注意：只有 note 消息才有 velocity；CC/程序变更等消息没有，
                        # 必须用 getattr，否则一收到非音符消息就崩溃。
                        raw_vel = getattr(msg, "velocity", 0)

                        # 2) 卡音看门狗：记录/释放被监控音符的按下状态
                        #    仅当该音在 stuck_notes 里、且原始力度 >= minvel 才记录，
                        #    从而只影响"硬击到 127"的那一下，正常弹不误伤。
                        if msg.type == "note_on" and raw_vel > 0:
                            if msg.note in stuck_notes and raw_vel >= stuck_minvel:
                                active_stuck[(msg.channel, msg.note)] = time.time()
                        elif msg.type == "note_off" or (msg.type == "note_on" and raw_vel == 0):
                            active_stuck.pop((msg.channel, msg.note), None)

                        # 3) 力度日志 + 整形（仅 note_on；v==0 的止音消息原样放行）
                        if msg.type == "note_on" and raw_vel > 0:
                            new_vel = transform_velocity(raw_vel, vel_scale, vel_power, curve_pts) if vel_enabled else None
                            if vel_log != 0 and (vel_log < 0 or vel_logged < vel_log):
                                vel_logged += 1
                                shown = new_vel if new_vel is not None else raw_vel
                                print(f"[{ts()}]  [力度] vel {raw_vel} -> {shown} {vel_bar(shown)}")
                            if new_vel is not None:
                                msg.velocity = new_vel

                        # 3.5) 全量输出：其余所有信号也打印；
                        #      CC 消息只在被 --show-cc 指定或被过滤时才显示
                        if all_log and not (msg.type == "control_change"
                                            and msg.control not in show_cc):
                            print(f"[{ts()}]  {msg_summary(msg)}")

                        try:
                            outport.send(msg)
                        except Exception:
                            print(f"[{ts()}]  输出端口异常，尝试重连...")
                            break
                    else:
                        now = time.time()
                        # 4) 卡音看门狗：超时无 note-off 的监控音符强制释放
                        if now - last_stuck_check >= STUCK_CHECK_INTERVAL:
                            last_stuck_check = now
                            if active_stuck:
                                for key, t0 in list(active_stuck.items()):
                                    if now - t0 > stuck_timeout:
                                        ch, note = key
                                        active_stuck.pop(key, None)
                                        try:
                                            outport.send(mido.Message('note_off', channel=ch, note=note, velocity=0))
                                            print(f"[{ts()}]  [卡音] 强制释放音符 {note} "
                                                  f"（按住>{stuck_timeout}s 仍无 note-off）")
                                        except Exception:
                                            pass
                        if now - last_check >= HEALTH_INTERVAL:
                            last_check = now
                            if not ports_healthy(cur_in, cur_out):
                                print(f"[{ts()}]  检测到设备断开，等待重新连接...")
                                break
                        time.sleep(0.001)

            print(f"[{ts()}] 本次连接结束，共丢弃 {dropped} 条弯音、{dropped_cc} 条 CC 消息，稍后重连...")

        except KeyboardInterrupt:
            print(f"[{ts()}] 已退出。")
            return
        except Exception as e:
            print(f"[{ts()}] 意外错误: {e}，稍后重连...")

        time.sleep(RECONNECT_DELAY)


def main():
    args = parse_args()

    if args.list:
        list_ports()
        return

    if not ensure_single_instance("Local\\MIDI_Pitch_Filter"):
        print("检测到另一个过滤器实例已在运行，本实例自动退出。")
        sys.exit(0)

    curve_pts = parse_curve(args.curve)
    stuck_notes = parse_stuck_notes(args.stuck_notes)
    block_cc = parse_cc_set(args.block_cc)
    show_cc = parse_cc_set(args.show_cc)

    auto_mode = args.auto
    if args.auto:
        in_name = out_name = None
        print("[自动模式] 由哨兵/脚本自动识别端口")
    elif len(args.ports) >= 2:
        in_name, out_name = args.ports[0], args.ports[1]
    else:
        ins, outs = list_ports()
        print()
        if args.vel_log > 0 and not (args.vel_scale != 1.0 or args.vel_power != 1.0 or curve_pts):
            print(f"[提示] 力度观察模式：下面选完端口、连通后弹琴，"
                  f"前 {args.vel_log} 个音符会打印 [力度] vel x -> x")
            print("       本模式不做整形，前后数值相同 = 这台琴的原始力度表现")
            print()
        in_name = pick(ins, ">>> 选择输入端口编号（你的键盘）: ")
        out_name = pick(outs, ">>> 选择输出端口编号（loopMIDI 虚拟口）: ")

    run(in_name, out_name, auto_mode, args.vel_scale, args.vel_power,
        curve_pts, args.vel_log, stuck_notes, args.stuck_timeout, args.stuck_minvel,
        block_cc, args.all_log, show_cc)


if __name__ == "__main__":
    main()
