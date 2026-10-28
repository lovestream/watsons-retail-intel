# 14｜DeepSeek 执行任务：给 QwenPaw 安装完全免费的本地中文 TTS（2026-10-29）

## 一句话决策

**首选 Sherpa-ONNX + MeloTTS Chinese ONNX，CPU 离线运行；不再依赖按字数收费的 SiliconFlow CosyVoice 做常态播客。** 用户已明确要求让 DeepSeek 在 QwenPaw 所在 NAS 安装。优先保留现有 Python、周/月/年报、MP3 邮件附件、事实质量门，**只更换 TTS 执行层**，不搬系统、不引入 GPU、不新建收费API。

官方权威参考：
- sherpa-onnx：https://github.com/k2-fsa/sherpa-onnx （Apache-2.0）；文档：https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html#vits-melo-tts-zh-en
- MeloTTS：https://github.com/myshell-ai/MeloTTS （MIT）；模型：https://huggingface.co/myshell-ai/MeloTTS-Chinese
- 已转换的中文/英文 ONNX 模型：https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-melo-tts-zh_en.tar.bz2
- pip：https://pypi.org/project/sherpa-onnx/2.0.1/ （2026-10-26已发布带Linux x86-64 wheel）。
- 群晖DS918+官方CPU：Intel Celeron J3455 4核、1.5GHz（burst 2.3GHz）；预装4GB，官方可扩至8GB。**此NAS没有用于大TTS的独立GPU**。需实测，不保证实时生成。官方Melo ONNX在Raspberry Pi 4/4线程测试约RTF 2.518（生成1分钟音频用约2.5分钟），这不是DS918+性能承诺。

**开源免费不等于没有电费、磁盘使用或下载流量，但本地推理本身不调用付费API。** 下载模型包的LICENSE需在安装前核实，尤其如果之后改用其它中文声音包。

## 当前代码的实际问题（按E1分支代码复核）

- 文件：skills/generate_periodic_report/generate_periodic_report.py，当前默认 PERIODIC_TTS_PROVIDER=siliconflow；_generate_tts_chunk先SiliconFlow失败才走edge-tts；_generate_tts有多轮rate_plan（+55%、+70%、+85%、+40%、-10%），每轮可能重新合成**整篇**口播稿，容易多次产生付费调用。
- 口播3,000–4,000中文字，7–9分钟目标；MP3产物会进入delivery gate，必须保留长度、可播放、脚本与产物SHA、真正TTS成功标记，不允许复制旧MP3冒充新音频。
- 只用本地单次合成、不调用硅基TTS/Edge网络；如TTS失败则本期音频、邮件交付应blocked，**绝对不能静默付费回退**。若日后想启用有偿回退，另经用户明确同意；目前配置默认PAID_FALLBACK=false。

## A. 现场预检：只读

1. 从实际QwenPaw容器、宿主/已挂载路径获取 uname -m、python3 --version、cat /proc/cpuinfo、free -h、df -h、ldd --version、grep -m1 flags /proc/cpuinfo，尤其留意NAS老CPU缺少AVX/AVX2、容器动态库和Python版本兼容问题。如果遇到Illegal instruction，不得改生产CPU/重装主环境，记录不兼容并换稳定的CPU兼容二进制或轻量模型。
2. 确认TTS模型和独立Python venv放在哪个**已经证实持久化的NAS挂载目录**，不要默认 /tmp、/etc、容器可写层重建后能保留，也不要直接pip install到QwenPaw现有venv/system Python。
3. 先确认NAS剩余可用RAM/CPU和模型下载空间；运行时最多2个TTS推理线程、一个作业，不占满QwenPaw交互服务；用nice/线程限额。模型model.onnx约163MB，加lexicon、venv、生成WAV/MP3临时文件应预留至少1GB。
4. 使用本地现成的W43口播稿测试，而不是为了TTS再调用付费文本大模型。测试不发送邮件。

## B. 安装：以下命令是可验证的参考模板，DeepSeek先替换唯一的持久路径

~~~bash
# 只在QwenPaw所在NAS容器内、确认持久化挂载后执行。
# 不能直接照抄 /path/to/...；先检查路径和可用空间。
export TTS_HOME="/path/to/verified-persistent-nas-mount/watsons-offline-tts"
test "$TTS_HOME" != "/path/to/verified-persistent-nas-mount/watsons-offline-tts" || { echo "Set the real persistent path first"; exit 1; }
mkdir -p "$TTS_HOME"/{models,tests,tmp}
python3 -m venv "$TTS_HOME/venv"
"$TTS_HOME/venv/bin/python" -m pip install --upgrade pip
"$TTS_HOME/venv/bin/python" -m pip install "sherpa-onnx==2.0.1"

cd "$TTS_HOME/models"
curl -fL --retry 3 -o vits-melo-tts-zh_en.tar.bz2 \
 "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-melo-tts-zh_en.tar.bz2"
tar -tjf vits-melo-tts-zh_en.tar.bz2 | head -30
tar -xjf vits-melo-tts-zh_en.tar.bz2
export MODEL="$TTS_HOME/models/vits-melo-tts-zh_en"
test -s "$MODEL/model.onnx" && test -s "$MODEL/tokens.txt" && test -s "$MODEL/lexicon.txt"
cat "$MODEL/LICENSE"
sha256sum "$MODEL/model.onnx" > "$TTS_HOME/model.sha256"

# 官方sherpa-onnx vits-melo-tts-zh_en CLI
"$TTS_HOME/venv/bin/sherpa-onnx-offline-tts" \
 --num-threads=2 \
 --vits-model="$MODEL/model.onnx" \
 --vits-lexicon="$MODEL/lexicon.txt" \
 --vits-tokens="$MODEL/tokens.txt" \
 --tts-rule-fsts="$MODEL/date.fst,$MODEL/number.fst" \
 --output-filename="$TTS_HOME/tests/zh-test.wav" \
 "本周屈臣氏经营情报。十月二十日，天猫双十一开卖。美团闪购、京东秒送，以及Sephora丝芙兰的会员活动值得关注。"

# 先用现成ffmpeg或imageio_ffmpeg带来的ffmpeg；若缺失只在隔离env配置。
ffmpeg -y -i "$TTS_HOME/tests/zh-test.wav" -codec:a libmp3lame -b:a 64k \
 "$TTS_HOME/tests/zh-test.mp3"
ffprobe -v error -show_entries format=duration,size -of json "$TTS_HOME/tests/zh-test.mp3"
~~~

如 2.0.1 wheel/运行不兼容，先查看pip/CPU/GLIBC/错误日志，再由官方发行包选择对应平台版本；严禁强制替换主进程依赖或为安装改PID1。若下载失败，使用NAS已允许的下载路径或授权代理，不要替换不明第三方模型包。

## C. 10秒/30秒、三分钟、完整播客的验收

- 先生成上述中英混排、数字、品牌样句，对比现有CosyVoice历史样例。重点听：普通话自然度、专有词（屈臣氏/天猫/京东/Sephora）、数字日期、停顿及尾音、节奏；**音质和合成速度必须同时评价**。
- 分别测约30秒、3分钟、W43完整口播稿：记录输入字符、合成墙钟时间、音频秒数、RTF=墙钟耗时/音频时长、CPU峰值/内存RSS、是否OOM及是否影响QwenPaw响应。RTF超过3或内存超限时，先降线程/优化分块或测试官方更轻量中文ONNX模型，不能自动升级GPU模型。
- 避免整篇文本一次塞进一个长CLI调用：按标点分割为100–300个汉字的片段，按顺序生成WAV；校验每片非零、可解码，拼接为PCM WAV后**只编码一次MP3**（ffmpeg），以免MP3分块拼接出现跳音。失败不复用旧片段/旧MP3；片段cache key需包含文本SHA、voice/model SHA、语速/版本。
- 调语速优先用一个合理的vits-length-scale（如0.9–1.0，实际官方CLI可用性先确认），若总体时长超标优先收紧口播稿字数并重新质检，而不是像现有代码一样以5档语速连续烧多遍TTS。**离线生成可以稍慢，不能为了速度牺牲人声自然度、干扰正常采集。**
- 如果MeloTTS不满足可听标准，第二候选在**同一sherpa-onnx引擎内**选更轻中文模型试听，如其官方Chinese VITS和Piper zh_CN模型；要分别核实MODEL_CARD许可证，有的中文Piper数据集标为Unknown，不能未经确认就宣称商业用途已获许可。不额外安装CosyVoice/F5-TTS/GPT-SoVITS大模型。

## D. 集成：只改TTS适配层，默认不产生API费用

1. 独立新分支，尽量不改已有R2/R3/M2/E1逻辑；在skills/generate_periodic_report下添加local_sherpa adapter。保留 _generate_tts 的返回接口 {"ok", "duration_seconds", "rate", "error"} 及delivery gate；追加provider=local_sherpa、script_sha256、model_sha256、audio_sha256、wall_seconds、rtf等**技术诊断指标**（不是价格账本）。
2. 设定PERIODIC_TTS_PROVIDER=local_sherpa（只在TTS测试与受控切换时使用），不要让 silliconflow 或 edge-tts 作为默认回退。明确配置如 PERIODIC_TTS_ALLOW_PAID_FALLBACK=false（新开关需自己实施，**默认false**）；任何错误不得自动请求 api.siliconflow.cn/v1/audio/speech。文本大模型SiliconFlow/RightCode路由不变！
3. 如需要直接在QwenPaw现有实例里使用，**用户本轮授权安装免费的TTS本地环境、模型及TTS局部集成**，但这不授权改动正式账本、生产Cron/PID1/Docker重启/SMTP或真实发信。对现役系统代码的TTS provider切换在完成隔离测试和可回退备份后进行，不要覆盖任何其他dirty文件。
4. 本地模型目录/venv必须在持久卷，TTS调用方式用该venv内binary或localhost unix service，**不要对公网开放HTTP端口**，不要重启现役QwenPaw主服务。保留失败返回和原报告证据状态。
5. 从最终口播稿->TTS音频->音频质量->电子邮件dry-run跑真实内容链路，复核mp3/mp3 duration，***不向真实邮箱投递***。核对发送接口仍保持只附MP3、禁Markdown；不修改邮件UX卡片简报新版分支。
6. 测试失效场景：model缺失、二进制无法运行、OOM、单chunk超时、生成文件损坏、意外触发付费接口；mock或拦截SiliconFlow TTS，确保无收费请求。长文自动重试最多失败片段一次，不重复全篇。失败按质量门阻止本期发送，日志只留错误原因，不输出Secrets。

## E. 完成标准与交付（一次执行，不要每一步请示）

- 在NAS真实生成**新**30秒中文MP3、3分钟MP3、完整周报MP3（如耗时过长或内存超限须如实报告），提供试听文件路径及数据摘要；
- 代码隔离分支+实际完整测试用例+W43 dry-run MP3/manifest（input/model/output SHA）+回滚入口，不损坏之前周报/播客；
- 完成中文+英文品牌与数字朗读的业务试听，不合格则不要启用；报告对比付费CosyVoice旧稿“声音自然度/单篇生成耗时/内存/失败率”。**费用结论只写本地推理无API按量费用，不需做金额成本估算**；
- 审查 _generate_tts 原5档重复合成问题，必须消除可能自动重复付费和回落SiliconFlow的路径；
- 提交 docs/reviews/NAS-LOCAL-TTS-INSTALL-VERIFICATION.md，列 exact NAS挂载持久路径（敏感路径脱敏）、版本/model LICENSE、脚本、Git SHA、实际RTF/资源、故障回退、是否TTS-only启用，所有真实操作与没有执行的动作分开说明。
- **本轮禁止**：未经授权的Docker/PID1重启、Cron启停、正式报告邮箱投递、旧历史账本更改、公开上传真实收件地址/密钥。不要因TTS安装顺手改邮件正文或报告事实。

**优先级：不需要最炫的声音，先做到中文自然可听、完全本地免费、NAS上可运行、可稳定产出有对应文本的MP3。**
