# 安装与跨宿主使用

适用于能够读取标准 `SKILL.md`、访问本机文件并执行本机脚本的个人电脑智能体。模型和 Harness 使用宿主能力，不需要 OpenAI、方舟或豆包 API 密钥。本轮未在豆包客户端实装验证，具体客户端须具备这些本地能力。

1. 解压私有 ZIP，保留完整 `feishu-bookkeeping/` 目录。包内含个人分类与固定账本标识，仅作个人私有分发，不上传公共仓库。
2. 准备 Python 3.11+ 和官方 `lark-cli`，使用本机已有的 `user` 登录授权。安装器不迁移登录令牌，不自动登录、切换身份或扩展权限。
3. 选择宿主的 Skill 目录运行安装器；省略 `--destination` 则在解压目录准备环境，再由宿主导入完整目录。不能仅上传一个 `SKILL.md` 而丢失执行器与私有资料。

```sh
python3.12 /解压目录/feishu-bookkeeping/scripts/install.py --destination /宿主技能目录/feishu-bookkeeping
```

安装器建立独立 `.venv` 并检查本地知识，不写入飞书。`--python` 可指定 Python 3.11+；`--offline` 只使用随包 wheels，包内 wheels 对应打包时个人电脑的 Python/系统平台，其他 Python 版本可在线安装相同依赖。遇到已存在的目标目录会停止，不覆盖已有安装。

Codex 默认安装位置为 `~/.codex/skills/feishu-bookkeeping`，下一轮对话可使用。其他宿主按其实际 Skill 导入路径安装，不猜测或修改客户端配置。`agents/openai.yaml` 仅为 Codex 元数据，其他宿主可忽略。

安装后只读字段检查：用安装目录 `.venv/bin/python` 执行 `scripts/bookkeeping.py`，stdin 为 `{"tool":"doctor","args":{"live":true}}`。失败时停止真实提交，不把本地模拟通过等同于连接成功。

生产状态在 macOS 的 `~/Library/Application Support/feishu-bookkeeping/` 下按固定账本隔离，Linux 为 `~/.local/share/feishu-bookkeeping/`。同机不同宿主的同一账本共用提交日志和进程锁，会话状态按 host/session_id 隔离。换宿主或移动安装目录不应复制、清空或更改生产状态。

本 Skill 只有两种开启方式：「记账小助手帮我记账」或明确要求用 `feishu-bookkeeping` Skill 记账。开启后当前会话持续生效，明确结束记账才关闭，新聊天重新唤醒。
