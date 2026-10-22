# Changes / 更新记录

## 2026-10-22

### English

- Count books completed today using Beijing time (`Asia/Shanghai`), independently of the device time zone. Refresh at Beijing midnight and on returning to the page; preserve de-duplicated completion days alongside progress.
- Share one PDF/page/policy-bound reading reference between translation and scoring. Check OCR consensus, filter illustration fragments and page furniture, and recover captions missed by full-page layout detection using overlapping strips. Keep recordings when text is uncertain; verify old score references before reuse.
- Prefer dictionary noun meanings for exact article-and-noun captions and trim translation loops without removing intentional repetition. Other sentences still use the existing translation model.
- Add narration and next-page actions inside pronunciation feedback. Space starts/stops recording or re-records; arrow keys turn pages from the feedback dialog. Hide PDF media-widget overlays while retaining bottom-toolbar narration.
- Add opt-in localhost-to-HTTPS account, progress, recording and result synchronization. Local reading scores directly on the local machine; durable, account-scoped outboxes retry background transfers. The default remains local-only.
- Extend generated-demo regressions and update English/Chinese usage and deployment documentation. Refresh original-demo screenshots. No commercial book content, private recordings, account data, model weights or credentials are part of this update.

The scoring model and formula are unchanged. Interaction tests use controlled recording/scoring fixtures; they do not measure real-world pronunciation accuracy. Existing historical completion dates are not guessed or rewritten by this source update.

### 简体中文

- “今日读完”按北京时间统计，不受设备时区影响；北京时间零点及返回页面时刷新。完成日期去重保存，与最后阅读位置分开。
- 翻译与评分统一使用绑定 PDF、页码及策略的正文，核对多轮 OCR，过滤图片碎字与页脚；整页布局漏掉句子时扫描横向区域。正文不确定时保留录音，旧评分重新使用前核对正文。
- 简短的“冠词 + 名词”说明优先使用词典名词释义，修正短句循环翻译并保留有意重复；其他句子继续使用原翻译模型。
- 跟读反馈内增加朗读、下一页；空格录音/停止/重读，方向键可直接从弹窗翻页。隐藏 PDF 内遮挡文字的媒体控件，保留底部朗读。
- 增加可选的本机到 HTTPS 云端同步：本机直接评分，账号范围内的进度、录音及评分后台同步，失败保留队列重试。默认仍只在本地使用。
- 补充原创演示回归测试、中英文使用和部署说明，更新原创演示截图。不包含商业绘本资料、私人录音、账号数据、模型或凭据。

评分模型与公式未调整。交互测试使用受控录音和评分替身，不代表真实发音识别准确率。本次源码更新不会猜测、改写历史完成日期。
