# Linux图形桌面录制补充

用户已确认目标环境为Linux图形桌面和浏览器，当前本地实际测试环境是Windows，因此本页的Linux采集入口属于待目标机验证能力。先执行cloud_preflight.py查前提；不把本地WGC/DDA案例当作Linux可靠性证据。

## 确认桌面及录制路线

按目标运行时获准接口正常观察桌面、浏览器和声音。查看XDG_SESSION_TYPE/DISPLAY、FFmpeg实际-devices、Pulse兼容音频服务和可用source/sink及应用路由。X11会话且FFmpeg实际具备x11grab/pulse时，可采用本包make_linux_capture_config.py构造明确的桌面区域+课程monitor输入参数，再交技能record_lesson.py执行。参数不是质量证明，尚需样片；窗口几何/坐标必须本轮观察，不读旧D盘配置。

Wayland会话没有在本包中提供已验证采集实现；XWayland应用不自动证明整个桌面可被x11grab捕获。优先目标环境已获准且已安装、通过正常屏幕共享授权的录屏能力，按工具实际文档测试。没有可用能力时记录阻塞，不静默改会话、启用权限、切X11、安装OBS或绕过门户授权。不要套用本包X11配置。FFmpeg输入支持说明见官方 https://ffmpeg.org/ffmpeg-devices.html#x11grab 及 https://ffmpeg.org/ffmpeg-devices.html#pulse 。

## 声音与窗口

课程浏览器流向已核的独立sink，录制输入使用该sink的monitor；monitor录系统输出不是麦克风设备。源名必须从当前音频服务实际列表确定，脚本仅用.monitor后缀做保守参数约束，不证明该设备是正确课程声道或真的存在。核其他应用没有混音、麦克风不是输入、物理声音策略与用户要求一致。PipeWire是否提供Pulse兼容接口须实际检查；不因服务名推断可采集。

区域必须是完整课程画面，保留标题、正文、字幕、水印，前台无遮挡。桌面区域采集依赖可见内容，不能与移动窗口/通知/锁屏并行。明确核正常播放2x、起点0及窗口后，先录30—60秒含讲话样片，正常q结束，实际查看/回放并核声音、黑屏/卡顿及同步；没有人听能力标限制，音轨不能代替声音质量。再归零启动每节独立连续录像，自然结束后正常收口。只确认缺课、无声、错课或严重卡顿时補录，字幕遮挡本身记录备注，不整节重录。

## 配置入口与停止

make_linux_capture_config.py只另存JSON，不启动FFmpeg、不改变音频或桌面、不安装。必填root/output/config、当前ffmpeg绝对路径、session-type=x11、display、x/y/width/height及已确认audio-monitor。输出是常见record_lesson.py配置，microphone_capture=false；其中现场验证字段固定false，执行者必须另行实际验证，不把生成配置当作可以开录。

在目标Linux实际同一解释器运行技能scripts/record_lesson.py配置文件；它通过显式arguments启动所属FFmpeg并写状态/log，源自然结束时创建对应停止标记正常q。初始参数使用libx264/AAC，此编码器是否可用须用目标FFmpeg检查；不支持时用同类获准能力另存配置，不强制安装。记录进程PID、frame/time、文件增长与源time共同前进，Linux使用正常进程工具而非Windows CIM，不按进程名批量终止。

## 其他工具与迁移

FFmpeg9恒定帧率限制仅用于页面扫描，不代表Linux录制必须等所有页面依赖备好。numpy/Pillow/ReportLab/pypdf/python-pptx/faster-whisper须Linux兼容安装；包内原Windows依赖不自动加入sys.path，模型数据是否兼容当前运行时要实测。转写绝对路径签名不能直接resume，另存新版本；原识别文本可只读边界审计。字体使用Linux当前可用CJK TTF/TTC显式路径，PDF/PPT实际渲染另核Poppler/LibreOffice。

本包只带继续录制所需技能、要求与接手工具；旧原件和Windows运行时不随包复制，不把历史工具参数自动迁到Linux。当前新圈选四项目录尚未核，不能跳过正常目录登记，也不把网站学习100%算作新录制完成。
