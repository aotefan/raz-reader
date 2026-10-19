# HTTPS 部署与 Mac 辅助评分

[English](DEPLOYMENT.md) · [中文首页](../README.zh-CN.md)

使用你自己管理的服务器和域名。以下域名均为占位示例，不会连接或修改作者的线上站点。

## 1. 准备服务器

按 README 安装 Linux 依赖，把项目放在 `/srv/little-reader`，使用专用服务用户运行 `./scripts/bootstrap.sh`。需要翻译和评分时另装可选环境；评分需要 Python 3.11，以及足够容纳 PyTorch 和模型的磁盘、内存。

自己的资料必须在这台服务器上运行 `scripts/import_library.py --root ...` 导入。书目包含绝对路径，不能直接把 Mac 的书目复制过去就当资料迁移完成。服务用户须能读取相应绘本与音频。

新建独立私人状态目录，由运行服务的用户持有：

```bash
mkdir -p data/cloud-state
chmod 700 data/cloud-state
python3 -m engine.server \
  --data data/cloud-state \
  --manifest data/manifest.json \
  --port 8765 --listen 127.0.0.1 \
  --cloud-origin https://reader.example.com
```

把域名换成自己的。长期运行应接入你的进程管理器；以上是前台运行示例，终端退出即停止。模型与词典仍放在项目 `data/` 下，`--data` 指定的是私人账号、录音等状态目录。

## 2. 配置 HTTPS 入口

域名解析到服务器，开放 80/443。按实际路径和域名修改 [Caddy 配置示例](../deploy/Caddyfile.example)。网站根目录必须是 `/srv/little-reader/web`，不能是整个项目或 `data`。

Caddy 公开静态界面，把 `/api/*` 转给回环地址后端，保留公网 Host / Origin 并传递真实客户端地址。账号 Cookie 和麦克风需要有效 HTTPS；不要把 8765 直接开放到公网。访问 `https://reader.example.com/api/health` 应显示 `cloud_full`，再验证注册、录音及第二个浏览器的数据隔离。

这是通用部署模板，不代表已经自动完成生产部署或安全审计。源码压缩包不包含个人数据备份，需要另外备份私人状态与资料。只对外提供有权公开的绘本。

## 3. 可选 Mac 加速

先安装并验证服务器评分模型，否则 Mac 离线时没有可用后备。Mac 上克隆项目，运行 `./scripts/setup_assessment.sh`，并安装 FFmpeg、eSpeak NG；Mac 需要能通过 443 访问同一个 HTTPS 域名。

共享凭据必须在服务器私有目录中生成，不打印到终端，不放进仓库。英文部署文档第 3 节给出了两段完整命令：先在服务器独占创建 `data/cloud-state/assessment-worker.key`；再通过已有安全管理通道把它传给 Mac，在 Mac 的 `data/mac-scorer/config.json` 中组合域名和密钥。目录权限为 0700，密钥和配置为 0600。创建服务器密钥后重启服务。

两段命令都使用独占创建，文件已存在时会失败，以避免误覆盖正在使用的凭据。不要把密钥放进截图、公开链接或 Issue。

配置好后在 Mac 执行：

```bash
python3 scripts/install_mac_scorer.py
```

启动项名称是 `org.littlereader.scorer`，用户登录后启动，异常退出后重启，不阻止正常休眠。不需要 Mac 入站端口或公开隧道，日志在被忽略的 `data/mac-scorer/` 内，不应公开。

停用 Mac 辅助评分，服务器继续工作：

```bash
launchctl bootout gui/$(id -u)/org.littlereader.scorer
```

恢复：

```bash
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/org.littlereader.scorer.plist"
```

Mac 缺席或繁忙直接走服务器；已领取任务后失联，心跳租约到期后回到服务器；持续连接但卡住还受总超时限制。正式使用前用自己的短录音验证两个路径。Mac 在线也不能代替网站服务器。

## 4. 小内存配置

源码保留紧凑模型导出和一致性检查工具，但需要针对自己的模型、环境和录音验证；先阅读工具 `--help` 与 [架构说明](ARCHITECTURE.zh-CN.md)。不要直接照搬私人部署的资源限额。公开包不包含生产 Compose 配置、服务器 IP、私人资源路径或机器凭据。
