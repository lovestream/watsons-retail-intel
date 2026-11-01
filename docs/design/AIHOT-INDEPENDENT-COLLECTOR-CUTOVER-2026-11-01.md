# AIHOT独立采集与内容管理底座：屈臣氏系统适配及切换方案（2026-11-01）

状态：**已写适配器和离线契约测试，未部署NAS，未访问生产AIHOT DB，未进行原始源全量联调。** 本变更基于PR #8隔离分支，在子PR继续保持Draft。

## 0. 技术与责任边界：明确采用此方案

```text
        ┌──────────────────────────────┐
        │ AIHOT (独立进程与数据库)          │
        │ RSS / web_list / JSON / 外部推送 │
        │ Worker + pg-boss + PostgreSQL │
        │ 文章ID、正文、时间、信源健康、修订    │
        └──────────────┬───────────────┘
                       │ SELECT-only 原始文章游标
                       v
        ┌──────────────────────────────┐
        │ watsons: AIHOT bridge (L0.5)  │
        │ 身份映射、来源过滤、原文日期保留     │
        │ 本地影子raw_articles_all.json  │
        └──────────────┬───────────────┘
                       v
        ┌──────────────────────────────┐
        │ Watsons业务准入 (PR #8)         │
        │ P01/P02/P03，事实与证据核验      │
        │ canonical_event / delta 去重  │
        │ 业务评分与P09，周/月/年情报      │
        └──────────────────────────────┘
```

### 谁负责什么

- **AIHOT唯一负责**：信源清单及采集频率、抓取失败/恢复、标准化URL/原稿身份、原始正文、文章修订、首见和原文发布时间、信源健康、采集运行日志。
- **Watsons唯一负责**：屈臣氏/即时零售/个护美妆业务准入、语义事实判定、经营价值评分、跨文章真实事件身份和实质更新、周/月/年编辑、播客、邮件质量门。
- **只读桥接**：AIHOT数据库仍是文章来源的权威；Watsons不写AIHOT表，不连接AIHOT公开API进行原始材料采集，不因AIHOT的热度或精选判断提升Watsons事件。AIHOT已完成文章级判重，不代表Watsons业务事实和跨新闻事件级去重完成。
- **两侧调度彻底解耦**：AIHOT的独立Worker继续按信源抓取；Watsons周期消费已经留存的证据。旧Watsons daily collect先保留、在影子结果完成验收并正式批准切换后才停用；绝不并行两套采集同时写一个生产raw路径。
- **避免重复付费**：AIHOT初期只开采集（`COLLECT_ENABLED=true`），模型开关初期`MODEL_CALLS_ENABLED=false`；Watsons后端按周/本期增量抽取所需资料。需确认关闭模型后正文抓取仍能按预期执行；如果不能，先在隔离测试定位，不擅自放开全量LLM费用。

## 1. 上游真实接口及版本依赖

源码验证：`KKKKhazix/AIHOT`，核对的main tree SHA `77cdcbfe60d9ccd57638c50810202670909bc2bf`。运行时**应锁定真实commit sha或发布tag**，不能以会变化的`main`作为长期部署基线。

- `database/migrations/0001_core.sql`：`articles(id, identity_key, url, title, published_at, discovered_at, updated_at, revision, content_hash, excerpt, body_text, body_status, backfill, source_id)`、`sources(id, name, tier, participation_mode)`；`article_discoveries`为来源分发链。
- `database/migrations/0089_publication_time.sql`：`articles.publication_time`另存发布时间精度，不把某天午夜误当分钟级真实时刻。
- `docs/sources.md`：6类来源，提供RSSHub私有主机显式放行`PRIVATE_FEED_HOSTS=rsshub`。首次初始化`industry/sources.json`导入种子；以后不自动删除已有信源，改在后台管理。
- `/api/v1/items`只包含已允许公开的资料，且通常仅24小时/7天窗口，不包含所有原始资料与原文。**不能拿公开API替代原文数据库桥**。
- PG连接：只读账户通过Docker内部受控网络访问`db:5432`，**不得**暴露5432到公网；只授权`SELECT`于`articles`和`sources`，设置`default_transaction_read_only=on`。连接信息放NAS密钥/环境变量`AIHOT_READONLY_DSN`，不写进GitHub、manifest或日志。适配器运行时另执行`SET TRANSACTION READ ONLY`。
- 版权：AIHOT对媒体正文只保留内部可分析的快照；原文的全文传播许可另行管理，Watsons邮件只使用有来源的简要事实/摘要，不转载来源全文。AIHOT的软件MIT许可**不包含AIHOT名称/Logo**及第三方字体，站点应自命名。

## 2. 隔离部署交接（须由NAS执行环境实际部署，当前未执行）

推荐独立项目目录：`/volume1/docker/retail-source/` 或NAS现有Docker管理目录，绝对不要覆盖现有`watsons-retail-intel`工作树。

1. **独立仓库**：从AIHOT官方仓库Fork/模板建立一个专门的经营信源站仓库，在其中锁定经过审核的AIHOT版本。当前连接器没有创建Fork或新仓库的权限；不能声称已建立独立AIHOT部署。
2. 在**首次数据库初始化之前**将`deploy/aihot-watsons/industry-sources.example.json`审核并复制到新AIHOT项目的`industry/sources.json`；否则AIHOT默认18个海外AI示例源会进入数据库。已有实例则通过后台停用不相关来源，seed文件不会替你删除它们。
3. 修改站点名称、Logo与后台入口，限定局域网管理访问。示例源现只给出4条复用当前Watsons `sources.yaml`的RSSHub路由，**并不代表这4条已实测可用**。其余正式竞对及官方信源须在AIHOT后台逐源预览后纳入；公众号/X接入有第三方付费Key，默认不上。
4. 独立PostgreSQL 17数据卷及数据备份；Node 24构建，Docker Compose的api、worker、web、db、setup；web端建议仅NAS LAN访问，数据库不发布端口。让AIHOT worker与现有RSSHub加入有明确授权的共享私有Docker网络；`PRIVATE_FEED_HOSTS=rsshub`**只放行已确认的主机名**。
5. 创建只读DB用户并仅授予所需表：
   ```sql
   CREATE ROLE watsons_aihot_ro LOGIN PASSWORD '<从密钥管理器配置的随机密码>';
   GRANT CONNECT ON DATABASE aihot TO watsons_aihot_ro;
   GRANT USAGE ON SCHEMA public TO watsons_aihot_ro;
   GRANT SELECT ON TABLE public.articles, public.sources TO watsons_aihot_ro;
   ALTER ROLE watsons_aihot_ro SET default_transaction_read_only = on;
   ```
   `<...>`仅为文档占位符，不要直接执行含示例密码的命令。数据库默认账号/密码不用于跨系统桥接。
6. 关闭AIHOT的内容公开/飞书推送/索引提交；初始`MODEL_CALLS_ENABLED=false`。尽量选择`participation_mode=isolated`的源，需在隔离环境核实其仍执行抓取、入库与正文提取。
7. 将`AIHOT_READONLY_DSN`提供给仅在内部Docker网络运行的Watsons桥接作业。安装`psycopg[binary]>=3`到桥接**独立环境**，不修改当前生产Python依赖。
8. 每次运行先仅打印JSON清单，不写任何文件；通过人工确认后才带`--write-shadow --output-dir ...shadow...`，生成影子`raw_articles_all.json`和`manifest.json`，再把它提供给Watsons现有filter的**隔离工作副本**，不得覆盖生产同名文件。

## 3. 可复现命令（脱敏）

### 不调用网络的离线输入

```bash
python3 -m unittest tests.test_aihot_bridge
python3 skills/aihot_bridge/aihot_bridge.py \
  --fixture tests/fixtures/aihot_articles.json \
  --source-id wt_36kr_watsons \
  --since 2026-10-30T00:00:00+08:00 \
  --until 2026-11-01T00:00:00+08:00 \
  --as-of 2026-10-31 \
  --write-shadow --output-dir /tmp/watsons-aihot-shadow
```
`tests/fixtures/aihot_articles.json`已提交，仅含明确标记为**合成测试**的`example.invalid`域名和虚拟文章，绝不是生产事实；实际契约测试也使用`tests/test_aihot_bridge.py`的合成内存数据。可进一步运行：`python3 scripts/preview_aihot_scope_shadow.py /tmp/watsons-aihot-shadow/raw_articles_all.json`，检查入口硬门的main/reference/reject分类。

### 连接AIHOT PG的只读影子导出

```bash
# AIHOT_READONLY_DSN由NAS secret store注入，不打印
python3 skills/aihot_bridge/aihot_bridge.py \
  --source-id wt_36kr_watsons \
  --source-id wt_36kr_beauty \
  --source-id wt_36kr_instant \
  --source-id wt_huxiu_watsons \
  --since 2026-10-30T00:00:00+08:00 \
  --until 2026-11-01T00:00:00+08:00 \
  --as-of 2026-10-31 \
  --write-shadow --output-dir /tmp/watsons-aihot-shadow
```

**分页/水位**：当前桥是明确时间窗口的完整快照，不是自动Cron增量游标。SQL按`updated_at,id`排序，默认最多2000行；查询多出一行立即失败，不输出部分文件，操作员缩小窗口或者明确调大`--max-rows`。正式投产前仍需加有事务幂等与水位回退保障的持久增量消费作业，不得只用“上次运行时间”或默认行数当作无遗漏。

## 4. 必须通过的隔离验收，不以“RSS很多条”衡量成功

- **可用性**：至少14天AIHOT稳定采集；所有来源有成功/失败/未触发状态，滚动源不会丢内容；AIHOT独立故障不能中断Watsons的已封账报告读取。
- **召回**：用户认可的屈臣氏共创、个护美妆竞对、山姆/盒马/奥乐齐县域零售渠道等已知样本必须有对应可定位原文或明确的`coverage_gap`；不允许补造不存在的资料。
- **噪声**：野人先生/股市/招聘/寿司郎/SAP等来源资料允许原样进入AIHOT**原始材料池**，但在Watsons业务准入后不得进入主事件池和任何周/月/年核心报告。
- **去重**：菜鸟新闻同原始文章23次抓取必须对Watsons导出为一个稳定文章ID；跨媒体转载属于同一`canonical_event_id`（此能力尚需完成），新增独立经营事实应保留更新。
- **原文与日期**：AIHOT文章无正文时`content=""`，不可复制模型摘要形成“原文证据”；未给真实发布时间时`published_at=""`，事件发生日期保持unknown，报告可使用“根据X在Y日的报道”口径。
- **成本**：每天提供raw/new/changed/duplicates/source_health、AIHOT免费抓取和增量模型次数；MODEL_CALLS关闭时AIHOT不产生预筛/评分费用。不得未经授权开启付费公众号、X或Jina抓取。
- **周期**：用冻结历史样本做业务范围、事实证据、同事件更新、周/月/年事实包离线影子回归；LLM周报和真实费用探针需另外批准。正式SMTP一直hold。
- **切换门**：新底座至少连续两周新增关键事件召回不低于旧管线，所有用户认可样本零漏放/误删，文章身份跨日幂等，停用旧collect不会丢本周源覆盖记录。通过后单独批准Cron切换与回滚；现阶段旧NAS生产链路不改。

## 5. 已交付与尚未完成

已交付：只读schema适配器、影子归一化输出、离线测试、AIHOT独立信源示例、部署/迁移/验收说明。

未完成：AIHOT私有独立Fork/模板仓库、NAS Docker启动、真实源预览与逐条标注、数据库只读用户建立、两周影子源覆盖、跨媒体canonical_event去重与真正增量事务游标、完整周/月/年端到端重生成。均不得宣称完成或已部署。
