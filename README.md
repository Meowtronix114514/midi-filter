# MIDI Filter Web Monitor

把 MIDI 键盘信号做**过滤 + 可视化监控**后转发到虚拟端口（loopMIDI）的小工具。
前后端分离：Python 后端收信号/过滤/转发 + SSE 推流；浏览器前端显示 LED 电平灯与 cmd 风格实时终端。

## 解决什么问题

键盘/设备会发出你不想要的信号，例如：
- **弯音轮（Pitch Bend）鬼畜** —— 推子自己乱跳
- **CC7 主音量推子** —— 有些键盘的音量推子会污染 DAW 音量
- 其它杂 CC / 时钟等

本工具在键盘与 DAW 之间做**白名单过滤**，只把有用的信号转发到 DAW，其余一律丢弃，
同时把每一条信号实时可视化，让你清楚"什么进了、什么被拦了"。

## 架构

```
键盘 ──USB──▶ midi_server.py（后端）
                ├─ MIDI引擎线程：白名单过滤 → 转发到 loopMIDI
                └─ HTTP+SSE 服务：电平事件推流
                              │ SSE (data: json)
                              ▼
             浏览器 http://127.0.0.1:8765 （前端 webui.html）
               ├─ LED 电平跳线条（NOTE/弯音/CC1/CC7/CC64）
               └─ cmd 风格实时终端（橙=被过滤，绿=透传）
```

## 白名单（默认）

| 信号 | 是否转发 DAW | 面板 |
|---|---|---|
| 击键 note_on/off | ✅ 放行 | 绿色电平条 |
| CC1 调制轮 | ✅ 放行 | 电平条 |
| CC64 延音踏板 | ✅ 放行 | ON/OFF 灯 |
| 弯音 pitchwheel | ❌ 丢弃 | 中点灯（双向） |
| CC7 主音量 | ❌ 丢弃 | 红色电平条 |
| 其它一切（杂 CC/时钟/程序变更…） | ❌ 丢弃 | 不显示 |

> 想调整白名单（例如改为禁用 CC1、或放行 CC7），编辑 `midi_server.py` 中
> `Engine.run()` 的 `control_change` 分支即可，改完重启。

## 依赖与运行

```bash
pip install -r requirements.txt    # mido + python-rtmidi
python midi_server.py              # 会自动打开浏览器
```

Windows 下也可以直接双击 `start.bat`。

需要先装好一个虚拟 MIDI 端口（如 loopMIDI），并把 DAW 的输入设为该端口。

## 文件

- `midi_pitch_filter.py` — 过滤/整形核心函数（力度条、CC 名称等，被后端复用）
- `midi_server.py` — 后端：MIDI 引擎 + 白名单过滤 + SSE 服务
- `webui.html` — 前端页面（LED 电平 + 终端）
- `start.bat` — Windows 启动脚本

## 技术细节

- MIDI 库：[mido](https://github.com/mido/mido) + [python-rtmidi](https://pypi.org/project/python-rtmidi/)
- 实时推送：SSE（Server-Sent Events），零额外依赖
- 端口：默认 `127.0.0.1:8765`（改 `midi_server.py` 顶部 `PORT`）
