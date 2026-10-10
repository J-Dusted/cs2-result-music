# CS2 胜负音乐注入实体麦克风

这个项目在 CS2 中根据胜负、地图开局/换边、本机 MVP 和炸弹爆炸倒计时
播放音乐，并把同一首音乐混入你实际使用的麦克风采集链（本机当前注册的是
`NVIDIA Broadcast`）。TeamSpeak 继续选择同一个麦克风，不需要额外的声卡
跳线，也不会把你和朋友的正常通话切走。

底层结构和 Soundpad 的关键点相同：不是在某个语音软件里做适配，而是在
麦克风的音频采集链里改变进入语音软件之前的 PCM 信号。这里使用官方
Equalizer APO 作为 APO 宿主，自研的 x64 VST2 DLL 负责混音。Equalizer
APO 会按它自身的标准安装方式设置 Windows 的 `DisableProtectedAudioDG`
值；不需要驱动测试签名、Secure Boot 改动或 WHQL 签名。

## 目录

```text
cs2-result-music/
  cs2_result_music.py        GSI、事件判断、曲目选择、命令发布
  config.example.json        主配置模板
  config.json                本地私有配置，已被 Git 忽略
  start.bat                  手动启动监听器
  build.ps1 / build_native.cmd
  bin/
    CS2MicInjector.dll       64 位 VST2 混音插件
    CS2LocalPlayback.exe     本机耳机试听播放器
    CS2MicInjectorTests.exe  原生自测程序
  deploy/
    cs2_result_music.txt     Equalizer APO Include 片段
  music/
    win/                     胜利音乐
    lose/                    失败音乐
    start/                   开局与换边音乐
    mvp/                     本机 MVP 音乐
    bomb10/                  炸弹爆炸前 10 秒音乐
  tests/
    test_cs2_result_music.py Python 单元测试
```

五个音乐目录互相独立，支持 `.mp3`、`.wav`、`.m4a`、`.aac`、`.flac`。
实际能否解码取决于 Windows 当前安装的 Media Foundation 解码器。

## 安装

### 1. 编译

需要 Visual Studio 2022 Build Tools 的 `v143` C++ 工具集和 Windows
SDK。进入项目目录后运行：

```powershell
.\build.ps1
.\bin\CS2MicInjectorTests.exe
```

`build.ps1` 会生成 x64 DLL、播放器和原生测试程序。原生测试覆盖无请求
透传、混音、曲目切换、软限幅、损坏路径回退，以及 Media Foundation
解码一条 WAV。

### 2. 安装 Equalizer APO

安装官方 Equalizer APO 1.4.2 或更新版本。安装完成后，在管理员
PowerShell 中执行：

```powershell
.\install.ps1
```

安装脚本主要做五件事：

- 把 `CS2MicInjector.dll` 放到 `C:\Program Files\EqualizerAPO\VSTPlugins`。
- 把 `deploy\cs2_result_music.txt` 放到 APO 配置目录，并仅在原
  `config.txt` 中追加一个带标记的 `Include`，不会覆盖已有 EQ。
- 创建 `C:\ProgramData\CS2ResultAudio` 并给 Windows 音频服务和当前
  用户读写权限。
- 创建 `music/win`、`music/lose`、`music/start`、`music/mvp` 和
  `music/bomb10` 五个曲库目录。
- 用 Equalizer APO 相同的注册规则，只给你指定的麦克风（`install.ps1`
  的 `-CaptureDeviceGuid` / `-ExpectedDeviceName`，默认 `NVIDIA Broadcast`）
  的 capture pre-mix 阶段启用 APO；不处理扬声器设备。

完成后按提示重启 Windows。

验证安装：

```powershell
python .\cs2_result_music.py --check-apo
```

如果刚重启后没有心跳，这是麦克风端点尚未被应用打开。可先运行：

```powershell
.\probe-capture.ps1
```

它会打开默认录音端点 5 秒；随后再执行 `--check-apo`。也可以直接用
TeamSpeak 占用麦克风并说话几秒。

插件加载后会在 `C:\ProgramData\CS2ResultAudio\status.txt` 周期写入心跳。

### 3. 生成 GSI 配置

将 `config.example.json` 复制为 `config.json`，再把 `gsi.token` 改成
一个只有你知道的随机字符串：

```json
{
  "gsi": {
    "bind_host": "127.0.0.1",
    "port": 3001,
    "token": "替换成随机字符串"
  }
}
```

不要与其他 GSI 配置共用同一个端口。存在多个
`gamestate_integration_*.cfg` 且它们都指向同一端口时，CS2 可能只采用
其中一个配置，表现为监听器完全收不到数据。本项目默认使用 `3001`。

然后执行：

```powershell
python .\cs2_result_music.py --install-gsi
```

它会在 CS2 的 `game\csgo\cfg` 目录写入
`gamestate_integration_cs2_result_music.cfg`，不会覆盖你原来的
`gamestate_integration_fast.cfg`。重启 CS2。

### 4. 放入音乐并启动

按需要把音乐分别放入：

```text
music/win
music/lose
music/start
music/mvp
music/bomb10
```

然后启动：

```powershell
.\start.bat
```

这个窗口用于接收 CS2 GSI 并判断事件，必须在 CS2 运行期间保持开启。
`--test` 只做一次手动音频测试，不会启动持续监听。

默认是手动启动。需要开机自启时运行：

```powershell
.\install-autostart.ps1
```

移除开机自启：

```powershell
.\uninstall-autostart.ps1
```

## 曲目模式

`config.json` 的 `playback` 下五个事件各自独立配置：
`win`、`lose`、`start`、`mvp`、`bomb10`。

随机播放：

```json
{"mode": "random", "specific": ""}
```

连续两次不会随机到同一首。顺序播放：

```json
{"mode": "sequential", "specific": ""}
```

按相对路径的文件名排序循环，下一首索引保存在
`sequence.json`。指定播放：

```json
{"mode": "specific", "specific": "champions.mp3"}
```

`specific` 必须是相对于对应音乐目录的路径，不能逃出该目录。

## 音频和 TeamSpeak

Python 只写一个原子替换的 `command.txt`。VST2 插件的后台线程每
50 ms 检查它，解码和文件读取都在后台完成，音频线程只复制麦克风
输入、叠加 PCM 和软限幅。

插件由 `audiodg.exe` 以 `LOCAL SERVICE` 身份加载，通常不能读取用户的
`Documents` 目录。因此控制器会先把选中的歌曲完整复制到
`C:\ProgramData\CS2ResultAudio\staging`，再把 staging 中的只读副本路径
写入 `command.txt`。复制完成后才发布播放命令，不会让插件读到半文件。

TeamSpeak 输入设备选择你注册插件的那个麦克风，本机当前是：

```text
麦克风 (NVIDIA Broadcast)
```

不要选择 VB-CABLE、VoiceMeeter 这类跳线虚拟声卡。为了不让实时音乐
被语音软件处理掉，建议关闭 TeamSpeak 的降噪、回声消除和自动增益。
项目的 APO 片段会在指定麦克风的条件块内把 `Preamp` 重置为 `0 dB`，
避免 Equalizer APO 默认配置里的 `-6 dB` 先压低你的人声。

音量由 `config.json` 里两个互相独立的参数控制，改完重启监听器生效：

- `audio.mic_gain_db`：混进麦克风的音乐音量，也就是朋友听到的大小。
- `local.gain_db`：本机耳机里的试听音量，只影响你自己。

dB 是对数刻度，每 `-6 dB` 约等于响度减半，每 `+6 dB` 约等于翻倍。默认
是 `-6 dB`。朋友嫌吵就往下调，例如 `-12` 或 `-15`；几乎听不到就往上调，
例如 `-3` 或 `0`。`Preamp` 保持 `0 dB` 不动，改它会把你的说话声也一起
压低。

本机试听由 `CS2LocalPlayback.exe` 独立播放到默认输出设备，和麦克风
注入插件使用同一个请求编号。两者会有几毫秒到几十毫秒的启动差异，
不会改变语音链路。

## 命令

```powershell
python .\cs2_result_music.py --check-audio
python .\cs2_result_music.py --check-apo
python .\cs2_result_music.py --test win
python .\cs2_result_music.py --test lose
python .\cs2_result_music.py --test start
python .\cs2_result_music.py --test mvp
python .\cs2_result_music.py --test bomb10
python .\cs2_result_music.py --install-gsi
python .\cs2_result_music.py --capture-gsi
```

`--test` 会像真实事件一样写入命令并要求插件播放对应目录的音乐。测试时
同时让朋友在 TeamSpeak 中确认麦克风端能听到音乐，再确认你说话时没有
中断。

`--capture-gsi` 会在正常监听的同时，把 CS2 发来的每个原始 JSON 快照
追加到 `work/gsi-capture.jsonl`。实战一局后可以检查其中的
`player.team`、`player.name`、`player.match_stats.mvps`、
`map.team_ct.score`、`map.team_t.score`、`map.phase`、
`round.win_team`、`bomb.countdown` 和 `phase_countdowns`，确认真实 GSI
数据与判定逻辑完全一致。

也可以直接双击或在终端运行：

```powershell
.\start-capture.bat
```

这个窗口会持续显示监听日志并保留原始 GSI。必须看到
`Listening on http://127.0.0.1:3001`，否则 CS2 不会把数据发到本项目。

## 触发规则

模板中的 `trigger.mode` 为 `round`，每个回合结算时播放。需要使用
整张地图结束时播放时，把 `config.json` 改为：

```json
{"trigger": {"mode": "match"}}
```

`match` 模式使用 `map.phase` 从非 `gameover` 到 `gameover` 的单次
跳变触发。

回合模式使用 `round.phase=over` 和 `round.win_team` 判定，胜负按
`player.team` 与回合胜方比较，并且每个回合最多触发一次。

- 玩家队伍来自 `player.team`，比分来自 `map.team_ct.score` 和
  `map.team_t.score`。
- 平局、缺少队伍、缺少比分、死斗和军备竞赛不播放。
- 下一张地图进入预热或实时阶段后重置事件状态。
- 同一局即使 GSI 连续发送多个 `gameover` 快照，也只触发一次。

新增事件：

- `start`：同一张地图观察过热身后，第一回合 `freezetime` 播放一次；同一
  张图内检测到 `player.team` 从 CT/T 切换并在新半场 `freezetime` 时再
  播放一次。
- `mvp`：只判断本机玩家。`player.name` 与
  `trigger.mvp.player_names` 去空格、大小写不敏感匹配；配置为空时只使用
  本机玩家名称。`player.match_stats.mvps` 增长时播放。
- `bomb10`：优先使用 `bomb.countdown`，其次使用
  `phase_countdowns`，字段缺失时从 `round.bomb=planted` 开始计时
  `trigger.bomb10.fallback_plant_seconds` 秒。默认在剩余 10 秒时播放，
  每回合最多一次，拆弹或回合结束后取消未触发的回退计时。
- 同一快照同时满足多个事件时优先级为
  `mvp`、`bomb10`、`start`、`win/lose`。

## 卸载

管理员 PowerShell 运行：

```powershell
.\uninstall.ps1
```

它只移除本项目追加的 Include、APO 配置片段和 DLL。加
`-RemoveState` 才会同时删除 `C:\ProgramData\CS2ResultAudio` 中的命令和
顺序索引。
