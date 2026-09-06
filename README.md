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

## 完整上手（Windows）

### 1. 安装虚拟 MIDI 端口（loopMIDI）—— 必须！这是"中转站"

本工具的工作方式：**键盘 → 工具（过滤）→ loopMIDI 虚拟口 → DAW**。
没有 loopMIDI 就相当于没有桥。

1. 下载：https://www.tobias-erichsen.de/software/loopmidi.html
2. 安装并打开 loopMIDI
3. 在下方文本框输入端口名（例如 `loopMIDI Port`），点 **+** 新建
   —— 端口名出现在列表里 = 虚拟口创建成功 ✅

### 2. 安装依赖并启动本工具

```bash
pip install -r requirements.txt
python midi_server.py        # Windows 下也可双击 start.bat
```

启动后浏览器自动打开 http://127.0.0.1:8765；
工具会自动连接「你的键盘 → loopMIDI 端口」。

### 3. DAW 里接入（关键一步）

以 Nuendo/Cubase 为例（其它 DAW 同理）：
**Studio → Studio Setup → MIDI Port Setup**

1. 找到 **loopMIDI 端口** → 勾选它的 **Input**
2. **物理键盘的 Input 不要勾** —— 信号已由本工具过滤并转发到 loopMIDI，
   若键盘直连 DAW 会造成信号重复/打架（血泪教训）
3. 轨道输入源选 **All MIDI Inputs**（或直接选 loopMIDI 端口）
4. 弹奏 → DAW 收到的就是"过滤后"的信号 🎹

## 文件

- `midi_pitch_filter.py` — 过滤/整形核心函数（力度条、CC 名称等，被后端复用）
- `midi_server.py` — 后端：MIDI 引擎 + 白名单过滤 + SSE 服务
- `webui.html` — 前端页面（LED 电平 + 终端）
- `start.bat` — Windows 启动脚本

## 技术细节

- MIDI 库：[mido](https://github.com/mido/mido) + [python-rtmidi](https://pypi.org/project/python-rtmidi/)
- 实时推送：SSE（Server-Sent Events），零额外依赖
- 端口：默认 `127.0.0.1:8765`（改 `midi_server.py` 顶部 `PORT`）
