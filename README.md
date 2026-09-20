# MIDI Filter & CC 监控台

把 MIDI 键盘信号做**过滤 + 力度整形 + 卡音看护**后转发到虚拟端口（loopMIDI），并附带一个**桌面端 CC 监控台**（tkinter，无网页、无后端服务）。

```
键盘 ──USB──▶ midi_pitch_filter.py ──▶ loopMIDI 虚拟端口 ──▶ DAW
                    │
                    └── cc_monitor_gui.py 可独立/并行使用：
                        实时监控 CC、手动调整 CC、动态增删监控项
```

## 解决什么问题

- **弯音轮（Pitch Bend）乱发** —— 过滤掉，不进 DAW
- **力度手感不像钢琴** —— 分段线性力度曲线，精确重映射
- **硬击某个键导致卡音（sustain 不停）** —— 按音符的"卡音看门狗"自动补发 note-off
- **想看/想调 CC（ modulation、踏板、音量…）** —— CC 监控台实时显示 + 手动注入

## 文件

| 文件 | 作用 |
|---|---|
| `midi_pitch_filter.py` | 核心过滤器：弯音过滤 + 力度整形 + 卡音看门狗，断线自动重连 |
| `cc_monitor_gui.py` | **桌面端 CC 监控台**（tkinter 窗口） |
| `cc_monitor.bat` | 双击启动 CC 监控台 |
| `start.bat` | 双击运行过滤器（交互选端口） |
| `requirements.txt` | mido + python-rtmidi |

## 过滤器用法（midi_pitch_filter.py）

```bash
pip install -r requirements.txt

python midi_pitch_filter.py --list                    # 列出 MIDI 端口
python midi_pitch_filter.py --auto                    # 自动识别端口，只过滤弯音
python midi_pitch_filter.py --curve "50:35,110:80,127:105"   # 钢琴手感力度曲线

# 卡音看门狗：只监控 note 60，硬击(原始力度>=127)超时未松开则自动释放
python midi_pitch_filter.py --auto --stuck-notes "60" --stuck-timeout 3 --stuck-minvel 127
```

### 卡音看门狗说明

- `--stuck-notes`：要监控的音符编号（逗号分隔，如 `"60,64"`）
- `--stuck-timeout`：超过该秒数仍没收到 note-off 就强制释放（建议 2~5 秒）
- `--stuck-minvel`：只有原始力度 ≥ 该值的敲击才启用（默认 127，对应"硬击才卡"的硬件特征）

> 为什么超时建议 2~5 秒而不是 0.1 秒？正常弹琴时 note-off 一松手就到、会立刻移出监控，
> 所以几秒的超时**不影响正常演奏**；只有 note-off 真丢了（真卡音）才会触发。
> 设太短（如 0.1s）会把每个正常按住的音都误判成卡音。

> 用键盘自带 Transpose 按钮移调时，同一物理键的 note 编号会变，需重新指定编号。

## CC 监控台用法（cc_monitor_gui.py）

```bash
python cc_monitor_gui.py     # 或双击 cc_monitor.bat
```

1. 选输入端口（你的键盘）和输出端口（loopMIDI），点**连接**
2. **实时监控表**：CC 号 / 名称 / 当前值 / 电平条 / 计数 / 最后时间
3. **动态增删监控项**：输入 CC 号（0~127）点"添加到监控"，或选中行"移除"——**运行中立即生效，无需重启**
4. **手动调整 CC**：选 CC 号 → 拖滑块/填数值 → "发送一次"，或勾"持续发送"每 200ms 重发
5. 可选：**转发所有消息到输出**（顶替过滤器在链路中的位置）、**过滤弯音**

## 完整上手（Windows）

1. 安装 [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) 并创建一个虚拟端口
2. `pip install -r requirements.txt`
3. 双击 `start.bat`（或运行过滤器）建立 键盘 → loopMIDI 通道
4. 需要看/调 CC 时，双击 `cc_monitor.bat`
5. DAW 里只勾 loopMIDI 端口的 Input，**物理键盘的 Input 不要勾**（避免信号重复）

## 技术细节

- MIDI 库：[mido](https://github.com/mido/mido) + [python-rtmidi](https://pypi.org/project/python-rtmidi/)
- Python 环境：[uv](https://docs.astral.sh/uv/) 管理的独立 CPython 3.12（不依赖任何第三方自带解释器）
- 桌面 GUI：tkinter（Python 自带，零额外依赖）
- 端口打开失败自动重试、设备拔插自动重连、单实例互斥锁

### 用 uv 重建环境（如需迁移/重装）

```bash
uv python install 3.12
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```
