# DreamLake Dataset 家族 API 设计

- 日期:2026-07-31
- 状态:设计稿 v3(未实施;v2 吸收第一轮 review 实证,v3 吸收第二轮方法级 review 与 `namespace/name` 限定名寻址,见 §14)
- 范围:`dreamlake-py` SDK 的 dataset API 重构 + Source 概念归位。SDK/CLI 现有 source 代码**暂不改动**,后续统一删除;server 侧只列影响,不展开

## 0. 决策清单(TL;DR)

1. **Source 归位为纯第三方存储接入**(s3 / dropbox / huggingface)。server 端 `kind="dreamdb"` connector 及其三个端点标记废弃;SDK/CLI 现有 source 代码保持现状,后续统一清理。
2. **Dataset 永远使用 dreamdb 存储**,通过 catalog 的 `schemaType` 字符串区分家族成员:预设类型(如 `video.annotation/v1`)有富可视化和富 SDK 方法;自定义类型走 UI raw view + SDK 通用方法。
3. SDK 以 **`Dataset` 通用基类 + schemaType 注册表分派** 组织:`Dataset.open(name)` 按 catalog 里的 schemaType 返回对应子类;现有 preset 更名为 `VideoAnnotationDataset` 并成为第一个注册子类。
4. **创建预设数据集的入口是类,不是字符串**:`VideoAnnotationDataset.create(...)` 参数完整类型化;schemaType 字符串只作为持久化分派键存在于 wire 上,在代码中仅出现一次(子类的 `SCHEMA_TYPE` 类属性)。
5. **自定义 schema 用声明式 `Schema`**(可序列化,与 manifest 同一 wire format),或者空建后动态 `add_track`(embedding 除外)。
6. **引入 `Track` 实体**:列式(单 track)读写全部收进 Track handle;行式(跨 track)读写留在 Dataset 上。
7. **写入语义为 append-only、write-once**:同一 (anchor, track) 重复写入结果未定义(dreamdb 引擎按内容序而非写入序解析同 anchor 冲突——第一轮 review 实证)。因此不提供 `set` 类更新方法。
8. Server 侧 v1 **零改动**;SDK `_platform.py` 需**小幅增改**(见 §7)。
9. 兼容性可随意破坏(尚无真实用户),但 `Dataset.open` 靠分派天然兼容旧数据。
10. 名称支持 **`namespace/name` 限定名**(不含 `/` = 自己的 namespace,完全兼容现状);server 端实证已支持 org 成员在组织 namespace 下创建/读写,零改动。

## 1. 背景与动机

原始需求:允许用户上传**自定义 schema** 的数据到平台并可视化。最初方案是在 Source 下新增 dreamdb 类型(server 已有半成品实现),经论证放弃,理由:

- `dreamlake.db` 的模块文档已经确立了分层意图——它是平台无关的 dreamdb 通用层,`dreamlake.dataset` 是"构建在其上的 preset"。本设计是补完这个已有分层,不是造新概念。
- Dataset catalog 的 `schemaType` 字段本来就是给可视化分派设计的自由字符串;`schemaJson` 本来就是给展示用的 schema 描述。基础设施是现成的。
- 权限:dataset 写权限 = namespace owner 或任意 org member;source 是 ADMIN-only(org member 连凭证都拿不到)。数据上传功能需要前者。
- 凭证 TTL:dataset 12 小时 vs source 固定 1 小时。
- 可见性:dataset 有 `private/public` + 公开 presign-read;source 没有可见性概念。
- 概念纯粹:Source = 外部数据接入,Dataset = 平台内数据,UI 按 schemaType 分派——三层对齐。

原 source 方案里"source item 可换绑数据源"的需求在本模型下自然消失(dataset 本身就是数据,不存在指针)。

## 2. 概念模型

```
Namespace
 └── DatasetEntry (catalog: name, schemaType, schemaJson, visibility, bucket)
      └── dreamdb space @ s3://…/datasets/<ns>/<name>, ref 固定 "main"
```

`schemaType` 是唯一的分派键,三个消费者共用同一条规则:

| 消费者 | 已注册 / 已知的 schemaType | 未知的 schemaType |
|---|---|---|
| SDK `Dataset.open` | 返回对应子类 | 返回通用基类(不报错) |
| web 可视化 | 富视图 | raw view(遍历 track + item) |
| `Dataset.list(schema_type=…)` | server 端过滤 | 同左 |

**未知即降级,永不拒绝**——新版本 SDK / 其他工具写入的数据,旧消费者永远能以通用方式读。

注意(第一轮 review 实证):dreamlake-ai 目前**没有任何 schemaType 分派逻辑,raw view 尚不存在**(dataset 详情页是静态原型 iframe)。浏览器端 dreamdb 读取能力已具备(`@dreamlake/dreamdb` 的 `Space.tracks()` / `readScalarColumn`),raw view 可行但是**未排期的独立交付物**。本设计 v1 的交付边界是 SDK 写入 + SDK 读回;raw view 作为配套 UI 工作单独排期,在其落地前自定义数据集"平台可见"仅到目录页为止。

schemaType 同时 stamp 两处:catalog 行(供平台分派)和 space meta `dreamdb.schema_type`(供离线 / 自托管场景分派)。现有 `db.py` 与 preset 已是这个做法,保持。

## 3. 类体系

```python
class Dataset:
    SCHEMA_TYPE: ClassVar[str | None] = None   # 子类填写;None = 通用基类,不注册

class VideoAnnotationDataset(Dataset):         # 原 dreamlake.dataset.Dataset 更名
    SCHEMA_TYPE = "video.annotation/v1"        # __init_subclass__ 自动注册进分派表
```

- 注册机制:`__init_subclass__` 读 `SCHEMA_TYPE` 非 None 即注册。字符串在代码中只出现这一次。
- 命名注意:类属性用常量风格 `SCHEMA_TYPE`,与实例属性 `ds.schema_type`(返回 catalog 实际值)区分,避免类属性遮蔽 property。
- 子类只允许**加方法、加校验**,不允许改变存储语义:任何预设数据集必须能被通用基类正确读出。为满足可替换性,preset 现有的 `tracks() -> List[TrackInfo]` 改为与基类一致返回 `list[Track]`(`TrackInfo` 删除,role/camera 等 preset 元信息并入 Track 的扩展属性)。
- 预设子类保留自己的严格性:`VideoAnnotationDataset.open` 遇 schemaType 不匹配报错(现 `_check_schema_type` 行为);其 `add_track` 保留 `x_` 前缀限制。通用基类的 `add_track` 无前缀限制——自定义数据集的整个命名空间属于用户。

### create / open 规则

| 调用 | 行为 |
|---|---|
| `VideoAnnotationDataset.create(name, preview_height=…)` | 预设创建的唯一入口,参数完整类型化 |
| `Dataset.create(name)` | 自定义数据集,空 schema(实证:dreamdb 支持空 `Schema()` 建 space),schemaType 默认 `"custom/v1"` |
| `Dataset.create(name, schema=sch, schema_type="acme.clips/v1")` | 自定义 schema + 自定义 stamp |
| `Dataset.create(name, schema_type="video.annotation/v1")` | **报错**,指路 `VideoAnnotationDataset.create` |
| `Dataset.open(name)` | 读 catalog schemaType → 分派;未注册 → 通用基类 |
| `VideoAnnotationDataset.open(name)` | 需要类型保证时用;mismatch 报错 |

设计取舍记录:review 曾建议裸 `Dataset.create(name)` 强制显式传 `schema=` 或 `schema_type=`(防旧 preset 用法误建)。不采纳——"空建后动态 add_track"是本设计的第一形态,且兼容性已确认无包袱;误用在首次 `add_episode` 时 AttributeError 快速失败,残留的空 catalog 行用 `Dataset.delete(name)` 清理即可。

### 名称寻址:`namespace/name` 限定名

所有接受 dataset 名称的入口(`create` / `open` / `delete`,含预设子类)统一支持限定名,解析规则:

| 输入 | 解析 |
|---|---|
| `"clips"`(不含 `/`) | 登录用户自己的 namespace(现行为,完全兼容) |
| `"acme/clips"`(恰好一个 `/`) | namespace `acme` 下的 `clips`;server 端鉴权(org member 可创建/写,见 §8) |
| `"@acme/clips"` | 同上——namespace 段容忍 `@` 前缀(server 的 `resolveNamespace` 已有此容忍,前端 URL 形式常被复制进 CLI/API 调用,SDK 对齐) |
| 多于一个 `/`、空段(`"/clips"`、`"acme/"`) | 报错:`"expected 'name' or 'namespace/name'"` |

- 无歧义保证:dataset 名称正则 `^[a-z0-9][a-z0-9._-]{0,63}$` 不含 `/`,限定名与裸名不会冲突。
- 解析单点实现(一个 helper,返回 `(namespace | None, name)`;`None` → 走 `get_namespace()` 解析自己的),所有入口复用,不各自 split。
- `Dataset.list()` 不接名称,改为增加 keyword 参数:`Dataset.list(namespace=None, schema_type=None)`,`None` = 自己的 namespace。
- handle 增加 `ds.namespace` 属性;`ds.name` 保持裸名;`DatasetInfo` 带 namespace 字段。
- 鉴权完全交给 server(403/404 权威);SDK 只把错误翻译成可操作信息(如 `"not a member of namespace 'acme'"`)。
- 约定分叉记录:legacy 函数式 API 用 `slug@namespace`(`robotics@alice`)。dataset 家族**有意**改用 `namespace/name`(GitHub / HuggingFace 风格,与前端 URL 路径 `/<namespace>/datasets/<name>` 同构);legacy 语法不迁移。

## 4. Schema(声明式)

纯粹的字段容器,不携带身份 / 行为(预设的差异在 Dataset 子类的方法上;预设的 schema 是子类内部 `build_schema()` 的实现细节)。

```python
sch = Schema()                                  # 方法名/参数与 dreamdb.Schema 完全一致,零转换层
sch.add_video("cam", mime="h264")
sch.add_image("thumb", mime="jpeg")
sch.add_image("meta", mime="json")              # JSON 文档 = dreamdb 惯用法,原样外露
sch.add_embedding("clip", dim=512, lsh_bits=14) # 只能在 create 时声明(LSH 索引是 schema 的一部分)
sch.add_scalar_string("label")
sch.add_scalar_int("frame_count")
sch.add_scalar_float("duration_s")
sch.add_scalar_bool("verified")
sch.add_scalar_categorical("category")
sch.add_scalar_timestamp("recorded_at")

sch = Schema.from_fields([{"name": "cam", "type": "video", "mime": "h264"}, …])
sch.to_fields()                                # round-trip;即 wire format
```

- **与 dreamdb 零转换层(用户决定)**:builder 方法名、参数与 `dreamdb.Schema` 完全一致(`add_video` / `add_image` / `add_embedding` / `add_scalar_*`);`add_track` 的 `kind` 用同一词汇表(`"video"` / `"image"` / `"embedding"` / `"scalar_float"` / …)——SDK 全程只有一套名字。JSON 文档即 `image` + `mime="json"` 惯用法,原样外露,不另造 `json` 类型 / `add_json` 糖。`required` 仅接受 False(传 True 报错并解释 add-only 演进)。自有 `Schema` 类仍存在的唯一原因:`dreamdb.Schema` 不可自省(声明写进去读不出来),我们的 Schema 是"会记录自己的 dreamdb.Schema"——同名同参,职责只是把声明同步落成 fields JSON(镜像 + catalog schemaJson)并做 fail-early 校验。
- **wire format**:`fields` JSON 数组(与 manifest 的 `fields` 块同一格式),`type` 即 dreamdb kind。
- 校验规则(fail-early,错误信息写"下一步做什么"):字段名 `^[a-z0-9][a-z0-9_]*$`;**保留名拒绝:`anchor`、`_anchor`、`_time_anchors`**(行式 API 的 `"anchor"` 键与 dreamdb 内部键不得被字段占用);重名拒绝;`video` 必须带 `mime`;`embedding` 必须带整数 `dim`。
- 编译到 `dreamdb.Schema` 时所有字段 `required=False`(add-only 演进的前提,与 preset 同理)。
- `audio` 维持排除(dreamdb `append_many` 尚不能 ingest;可声明但填不进的字段只会误导——commit fc7d56d 的既有决策)。

### fields 镜像(schema 自省的实现机制)

第一轮 review 实证:**dreamdb 0.0.5 没有 schema/track 自省 API**(Python 侧读不回字段列表与 kind)。因此 `ds.tracks()` / `ds.track()` 的急切校验、Track 的 kind 门控,全部依赖 SDK 在 **space meta 维护 fields JSON 镜像**(键 `dreamdb.dataset.fields`——实施时发现 dreamdb 的 `set_meta` 强制 `dreamdb.` 前缀,故不能用 `dreamlake.*`;机制与 preset 的 `USER_TRACKS_META_KEY` 同构):create 写入全量,`add_track` 追加更新。已知局限,写进文档:经 `ds.db` 逃生口直接加的字段镜像不可见;其他进程 `add_track` 后需 `ds.reload()`(§7)才可见。根本解法是 dreamdb 提供 schema 自省 API,列为上游需求。

catalog `schemaJson` 同步:create 时写入 `to_fields()` JSON(目录页展示用);动态 `add_track` 后允许过期,v1 接受(见 §8 可选 server 改动)。

## 5. Track 实体与读写 API

### 分层原则

数据有两种到达形状,对应两个层级:**行式(一个 anchor 横跨多个 track)在 Dataset 上;列式(单条信号)全部在 Track handle 上**。两层最终都编译成 dreamdb `append_many` 的稀疏行(视频除外,走 `ingest_cmaf`);值归一化规则两层共用。读路径由 dreamdb `iter_all_batches(fields, start_ns, end_ns)` 支撑(实证存在,非视频 kind 的往返可实现)。

### Dataset 层(行式)

```python
ds.append_rows(rows)                     # 每行 dict 必带 "anchor" 键;稀疏行合法;单行就是 [{…}]
ds.rows(start=…, end=…, tracks=None)     # 行读;默认排除 video track(见下)
```

设计取舍(第二轮 review):**不设单行 `ds.append(anchor, values)`**——它恰等于一行的 `append_rows`,而"招牌单行动词"会诱导 §6 明令避免的逐点提交循环;每种形状只留一条写路径,提交成本保持可见。单点列式写已有 `t.append(t0, v)`。

### Track 层(列式)

```python
t = ds.track("temp")                       # 打开 handle;未声明 → 报错并指路 add_track
t = ds.add_track("temp", kind="scalar_float")  # 声明(幂等,变 kind 报错),返回 handle
ds.tracks()                                # -> list[Track]

t.append(anchor, value)                    # 点写:向特定 anchor 追加一个数据点   ┐ 点对
t.get(anchor)                              # 点读;无值返回 None                   ┘
t.append_range([(t0, v0), (t1, v1)])       # 区间写:向时间线追加一段区间数据      ┐ 区间对
t.read(start=…, end=…)                     # 区间读;未写过的已声明 track 读回 []  ┘
t.name, t.kind, t.mime, t.dim              # 元信息(dim 仅 embedding)

# 仅 video kind;height=None 无损 remux,height=N 重编码;返回 ingest 摘要
ds.track("cam").ingest("./clip.mp4", anchor=t0, frag_seconds=2.0, height=None)
```

- 不设 `t.set`:引擎无法提供覆盖语义(§6),名为 `set` 的方法会许诺做不到的事。
- **写动词族:一个动词 `append`,后缀编码形状(用户决定,取代早先的 append/extend)**:所有写入本质都是 append(存储 append-only),点与区间的区分放进名字——`t.append(anchor, value)` 无后缀 = 向**特定 anchor** 追加一个数据点(签名本身写明 anchor;事件、标注、不规则采样);`t.append_range(items)` = 向时间线追加**一段区间数据**(items 为 `(anchor, value)` 序列,SDK 按 anchor 排序后写入,批内重复 anchor 报错;等间隔信号配 `sequence_anchors`:`t.append_range(zip(sequence_anchors(len(vs), start=t0, step=…), vs))`)。Dataset 层的 `ds.append_rows` 同族:无后缀 = 点,`_rows` = 行式(跨 track),`_range` = 列式区间。命名取舍记录:`put/write`(与 get/read 对仗)被否——put 隐含覆盖语义(同 `set` 被否的理由),write 不表达区间;`extend` 被否——只表达"多个"不表达"区间"。提交粒度提醒同 §6:每次调用都是一次提交,循环逐点 `append` 会造 ref churn,成段数据用 `append_range` 一次进。
- handle 廉价、急切校验(从 fields 镜像解析一次元信息);不存在立刻报 `"no track 'x' — declare it first with ds.add_track(...)"`。
- **单个 `Track` 类,按 kind 门控**,不做九个子类:video track 上 `append` 报错指向 `.ingest`;非 video 上 `.ingest` 报错。
- `add_track(kind="embedding")` 报错(create-time only,与 preset 同一约束、同一措辞)。
- 命名依据:handle 工厂用名词方法(`ds.track(name)` / `ds.tracks()`,同 `ds.episode(id)` 惯例);handle 上的动词不需要宾语后缀。
- **空读规则(契约)**:已声明但从未写入的 track,`t.read` 返回 `[]`、`t.get` 返回 `None`、`ds.rows` 缺键,**不报错**——dreamdb 对无数据 track 抛 `"no FieldTrack"`,SDK 吞掉(preset 现行做法)。否则 `add_track` 后立刻 `read` 就炸,是必然的 day-1 bug。
- **`t.ingest` 语义**:`height=None`(默认)= 无损 remux,受 dreamdb init-segment 一致性约束——同一 track 所有片段必须同编码档,错配时报错并解释(preset 为此把编码档钉在数据集生命周期上);`height=N` = 经 `_ffmpeg.fragment_video` 重编码为统一档。返回摘要 dict(片段数、覆盖区间)。写入前 SDK 做 `[anchor, anchor+duration)` 与既有覆盖的重叠预检(引擎重叠行为未验证,§11.3,预检兜底)。

### 写侧:原生表示直接透传,校验才是主体

写侧第一原则:**传 dreamdb 原生表示(`bytes` / int ns / 原生标量 / float32 向量)就原样透传,零转换开销**——不存在必须经过的转换层。SDK 在写侧真正做的事是**校验**:kind 匹配(据 fields 镜像)、embedding dim、video 拒写指路 `.ingest`、批内重复 anchor——把本会在 Rust 引擎深处(甚至部分字节已落 S3 之后)爆出的错误提前到调用点并给出可操作信息。在此之上额外接受少量常见 Python 输入形态(单向便利,fail-early):

| kind | 接受的 Python 值 | 归一化 |
|---|---|---|
| `scalar_float/int/bool/string/categorical` | 对应原生标量 | 严格类型,拒绝有损隐式转换 |
| `scalar_timestamp` | int(ns)或 tz-aware `datetime` | datetime → ns |
| `image` | `bytes`,或 `str/Path` 文件路径;`mime="json"` 时另接受 `dict` / `list` | 路径 → 读字节;dict → JSON 序列化 |
| `embedding` | `list[float]` / `np.ndarray` / `.npy` 路径 | 统一 float32,dim 不符报错 |
| `video` | — | `append` 一律拒绝,指路 `.ingest` |

`None` 一律拒绝(实证:`append_many` 拒绝 None)——缺值 = 不写该键,稀疏行本身就是缺值的表达。

### 读侧返回:存储表示原样返回,仅一个例外

不做第二套读侧类型系统:`scalar_*` 返回原生标量(timestamp 为 int ns)、`image` 返回 `bytes`、`embedding` 返回 `list[float]`——即存储表示。**唯一例外:`mime="json"` 的 image track 读回解码后的 `dict` / `list`**——写侧接受 dict 是既定便利,不解码就成了"写 dict、读 bytes",往返契约被破坏(preset 读标注也是解码返回)。写侧归一化(路径→bytes、datetime→ns、`.npy`→向量)是单向的输入便利,不是类型系统。`video`:`ds.rows` 默认排除;`t.read` 报错指路(v1 无区间视频读;播放走平台,原始字节走 `.db`)。

### 往返对称契约(正式契约,含边界)

`ds.rows()` 返回形状 = `ds.append_rows()` 输入形状;`t.read()` 返回形状 = `t.append_range()` 输入形状。`ds2.append_rows(ds1.rows(…))` 与 `t2.append_range(t1.read(…))` 无需转换成立。边界:**缺失字段 = 键不存在,绝不 None 填充**(否则回写被拒);**video track 不参与行读**(其"行值"是 CMAF 片段字节,无行语义且不可回写),`tracks=` 显式点名 video 时报错指路。

## 6. 语义细则

- **anchor**:绝对 int 纳秒;所有接受 anchor / 区间的参数(`anchor`、`start`、`end`)同时接受 **tz-aware** `datetime`(自动换算);naive datetime 报错("pass tz-aware datetime or int ns"),不猜时区。参数名用 `start`/`end`(不带 `_ns` 后缀——后缀会与"也接受 datetime"自相矛盾)。顺序数据(有 N 行、无时间戳)提供确定性 helper:`sequence_anchors(n, *, start=0, step=1) -> list[int]`——anchor 即行号;续传自 `ds.anchors()[-1] + 1`。不做隐式位置推导。
- **写入 = append-only、write-once(v1 核心语义)**。同一 (anchor, track) 重复写入**结果未定义**:实证 dreamdb 引擎按**内容序**(非写入序)解析同 anchor 冲突,后写的值可能读回旧值。SDK 在同一批次内检测到重复 (anchor, track) 即报错(廉价的 fail-early);跨调用无法检测,靠文档约定。需要修订语义的场景:preset 的 revision-window 模式(anchor+k 错位)是已验证的实现路径,留作 v2 的 update API,v1 不做。
- **稀疏行合并(实证通过)**:不同字段在同一 anchor 经多次 `append_many` 写入,读侧按 anchor 合并——列式 `t.append` 可直接生成稀疏行,无需客户端预合并。
- **提交粒度**:每次 `append*` / `ingest` 调用 = 一次 dreamdb 提交(新 manifest + ref 前进)。文档明确"批量数据一次进";逐点循环 = 大量小提交 + ref churn。增量流式需求留给后续的带缓冲 `ds.writer()`,v1 不做。
- **并发**:v1 明确**单写者约定**(每次提交推进 ref `main`,并发写者会产生 CAS 冲突 / 丢失更新)。冲突以 `DatasetError` 显现,信息指明单写者约定。多读者不受限。
- **凭证 lease**:open/create 时 broker 一次(12h TTL),expiration stamp 在 handle 上;**每个触达 S3 的操作前检查过期**,过期抛 `DatasetError("credentials expired — call ds.reload()")`(现状是裸 S3 错误;`reload()` 见 §7,重新 broker 走 HTTP token 认证,与过期的 S3 凭证无关)。已知局限:凭证经进程级 AWS 环境变量生效,**同进程同时活跃多个平台数据集会互相覆盖**(每个 lease 只授权自己的前缀)——v1 文档限定"一个进程一个活跃平台数据集";根本解法(向 dreamdb backend 显式传凭证)列为上游需求。
- **读侧规模**:`rows` / `read` v1 返回 list;无范围参数 = 全量,文档提示大数据集应传范围。流式迭代器留作后续。
- **错误**:沿用 `DatasetError(RuntimeError)` 家族(`DatasetExistsError` / `DatasetNotFoundError` 已在 `_platform`),新增 `SchemaError(DatasetError)`。信息一律写"下一步该做什么"。

## 7. 平台交互与通用方法集

通用基类的完整表面(预设子类在此之上加富方法):

```python
# 生命周期(name 均接受限定名 "namespace/name",§3;delete 为 classmethod:无需 open、无需 broker 凭证)
Dataset.create(name, *, schema=None, schema_type=None, visibility="private")
Dataset.ensure(name, *, schema=None, schema_type=None, visibility="private")   # open-or-create
Dataset.open(name)
Dataset.list(namespace=None, schema_type=None) -> list[DatasetInfo]
Dataset.delete(name, *, purge=False)

# 结构(tracks 即 schema 的活形态,不设 ds.schema,见下)
ds.add_track(name, kind, *, mime=None) -> Track
ds.track(name) -> Track;  ds.tracks() -> list[Track]

# 行式读写(§5)与自省
ds.append_rows(rows);  ds.rows(start=, end=, tracks=)
ds.anchors(start=None, end=None) -> list[int]   # 计数(len)/跨度(首尾)/落库验证的原语
ds.reload() -> ds                        # 就地刷新 catalog 行 + fields 镜像 + 凭证 lease;Track handle 存活

# 元数据
ds.namespace; ds.name
ds.schema_type; ds.visibility; ds.set_visibility(v)
ds.db                                    # dreamdb.Dataset 逃生口(沿袭 preset 的 .db)
```

- `Dataset.delete` 做成 classmethod 还有一个动机:与逃生口 `ds.db.delete(anchors)`(dreamdb 的行墓碑删除)拉开距离——同名实例方法一属性之隔、破坏性含义不同,是事故源。行级删除 v1 仅经逃生口可达,`ds.delete_rows(anchors)` 留作后续。
- **不设 `ds.schema`**:`ds.tracks()` 返回的就是 schema 的活形态(每个 Track 携带 name/kind/mime/dim),再挂一个返回 `Schema` 的属性是同一事实的第二种表示。由此 `Schema` 收窄为纯**写侧声明容器**——只作为 `create`/`ensure` 的输入和 wire format 存在,永远不从数据集读回。(不设 `Schema.from_tracks`:track 与 schema 并非一一对应,克隆场景暂无需求——用户决定。)
- `Dataset.ensure`:open-or-create,可重跑上传脚本的第一需求(preset 自己的文档就围绕 try open / except create 惯用法设计——模式已被房规承认,基类吸收样板)。语义:不存在则 create;存在则 open,schema_type 不匹配报错,传了 `schema=` 时校验声明字段已在镜像中。
- `ds.anchors()`:走 dreamdb `list_anchors()`;**不用 `count()`**——0.0.5 的 count 经 `iter_stream` 实现、仅支持 embedding,纯标量数据集会炸(第二轮 review 实证)。
- `ds.reload()`:就地刷新的统一恢复动词,对应两处已文档化的失效(跨进程 `add_track` 的镜像过期、12h 凭证 lease 过期)。重读 catalog 行 + fields 镜像、重新 broker lease、换内层 dreamdb handle;`Track` handle 引用 `ds`,全部存活——比"重新 open"少丢一层对象身份。
- schemaType → 类的注册表 v1 为**私有**(`_registry`):公开的 `Dataset.for_type` 没有 v1 消费者(TS CLI 不调 Python,Python CLI 已废弃),待有真实消费者再公开。
- `DatasetInfo`(frozen dataclass)钉到 server list 响应 `toSummary` 形状 + namespace:`name` / `namespace` / `schema_type` / `visibility`;`schema_json` / `bucket` 仅存在于 detail GET,不进 `DatasetInfo`。
- **不公开 `set_meta` / `get_meta`(经质询裁撤)**:dreamdb space meta 是**空间级全局 KV**——存在 S3 侧、随数据集走,**不是 track,与 anchor 时间线无关**,每键一值。系统一直在内部使用它(`dreamdb.schema_type` 分派 stamp、fields 镜像 `dreamlake.dataset.fields`、preset 的 `dreamdb.dataset.encoding` / `dreamdb.title`),但用户级全局 KV 目前没有真实需求,不为它占公共表面。镜像/stamp 走内部 guarded 通道;**逐 anchor 的元数据本来就该声明成 `image`+`mime="json"` track**;确有全局 KV 需求时经 `ds.db.set_meta`(文档警告勿碰 `dreamdb.` / `dreamlake.` 保留前缀),出现真实场景再公开 guarded 包装,恢复成本一行。
- `embed` / `search` 等依赖平台服务的能力**不进**通用基类,留在预设子类。
- **`_platform.py` 需小幅增改**(修正 v1 文档"无需改动"的说法,第一轮 review 核实):
  1. `open_dataset` 目前不解析 catalog GET 响应体(只查状态)——需返回 catalog 行,`Dataset.open` 的零额外请求分派依赖它;
  2. 新增 PATCH helper(`set_visibility` 用;将来 schemaJson 刷新也走它);
  3. 新增限定名解析 helper(返回 `(namespace | None, name)`,§3)并把 namespace 参数贯通到各函数(现在全部内部解析登录者自己的 namespace);
  4. 其余(`create_dataset` 的 `schema_type`/`schema_json` 参数、凭证代理含 MinIO 空 sessionToken guard、`list_datasets`/`delete_dataset`)原样复用。

## 8. Server 侧影响

- **v1 必需改动:无**(第一轮 review 逐条核实:POST 接受任意 `schemaType` ≤200 字符 + `schemaJson` ≤100KB;GET/list 响应含 `schemaType`;凭证 [900, 43200]s 默认 12h、member 门槛;public presign-read 存在;PATCH 仅 visibility)。
- 可选:catalog PATCH 放开 `schemaJson`,供动态 add_track 后刷新目录页展示。
- 后续(网页端创作自定义数据集时):把 source 路由下的浏览器写通道(`/dreamdb/sign` + `/dreamdb/ref`)移植到 datasets 路由。v1 不做。
- UI 侧:raw view 是**独立交付物**(§2 注意事项),其分派规则即 §2 表格;raw view 从 space 枚举 track,不依赖 catalog schemaJson 的新鲜度。

## 9. 废弃与清理(全部延后,本期不动)

| 对象 | 处置 |
|---|---|
| server `kind="dreamdb"` connector + `/dreamdb/{sign,ref,credentials}` 端点 | 标记废弃,暂不删除 |
| Python CLI `dreamlake source` 子命令 | **保持现状,后续统一删除**(用户决定)。届时值得抬进 SDK 的资产:fields 校验规则(→ `Schema`)、`_load_vector`(→ embedding 归一化) |
| `dreamlake.db` | 保持现状:平台无关的底层逃生口 |
| TS `dreamlake-cli` | 后续镜像 dataset 家族命令(create / push,schema.json + manifest 复用同一 wire format) |

视频路径统一:ffmpeg 复用 `dataset/_ffmpeg.fragment_video`,写入走**公开的** `dreamdb.Dataset.ingest_cmaf`(preset 现行做法;不碰 `db._fragment_video` / `_inner._ingest_cmaf` 私有 API)。

## 10. 兼容性变化(已确认可破坏)

- `dreamlake.dataset.Dataset` 从"preset 类"变为"通用基类 + 工厂";preset 更名 `VideoAnnotationDataset`。
- `Dataset.open("旧的 preset 数据集")` 靠分派**完全兼容**(返回正确子类)。
- 裸 `Dataset.create("x")` 语义变化:原来建 video.annotation 预设,现在建空自定义数据集(取舍记录见 §3)。
- preset `tracks()` 返回类型从 `List[TrackInfo]` 改为 `list[Track]`(§3 可替换性)。
- 顶层懒导出 `dreamlake.Dataset` 跟随指向通用基类;`VideoAnnotationDataset` 同样懒导出。

## 11. 验证点状态(第一轮 review 实证更新)

| # | 问题 | 状态 |
|---|---|---|
| 1 | 空 `Schema()` 建 space | ✅ 支持(实测通过,空建 + add_track + append 全通) |
| 2 | 稀疏行同 anchor 跨字段合并 | ✅ 按 anchor 合并(实测);**同字段同 anchor 为内容序**,已定为 write-once 语义(§6) |
| 3 | 视频区间 `[anchor, anchor+duration]` 重叠行为 | ⏳ 未验证;SDK 应尽量提前检查报错 |
| 4 | `append_many` 各类型 ingest 能力清单 | 部分:`None` 拒绝(实测)、audio 不支持(已知);其余待清点 |
| 5 | dreamdb schema 自省 API | ❌ 不存在(实测),fields 镜像方案落定(§4);自省 API 列为上游需求 |

## 12. 模块落位

```
dreamlake/dataset/          (实施后的实际布局)
  __init__.py            # 导出 Dataset, VideoAnnotationDataset, Schema, Track, DatasetInfo,
                         #   sequence_anchors, DatasetError, SchemaError + preset 既有导出
  _errors.py             # DatasetError / SchemaError(通用层与 preset 共享,避免互相 import)
  _base.py               # 通用 Dataset 基类 + __init_subclass__ 注册表 + DatasetInfo
  _track.py              # Track 实体 + 值校验/编解码
  _fields.py             # 声明式 Schema + fields wire format + to_anchor_ns/sequence_anchors
  _core.py               # 预设子类 VideoAnnotationDataset(保留原文件名,类改名并继承基类;
                         #   TrackInfo 删除,tracks() 返回 list[Track])
  _schema.py             # preset 常量/校验(保留原位,TrackInfo 移除)
  _episode.py            # 不动(preset 专属)
  _ffmpeg.py             # 不动(通用 ingest 与 preset 共用)
_platform.py             # 增改:split_qualified、namespace 贯通、get_dataset、
                         #   open_dataset(row=) 短路、patch_dataset、resolve_namespace、403 文案
db.py                    # 不动
```

## 13. 未决问题

1. 自定义默认 schemaType 的具体字符串(暂定 `"custom/v1"`;UI 规则是"未知即 raw view",则此字符串无需特殊地位)。
2. 预设类的最终命名(`VideoAnnotationDataset` 直白但长;备选 `EpisodeDataset` / `RobotDataset`)。
3. raw view(dreamlake-ai)的排期与负责人(§2)。

## 14. 修订记录

- **`video.annotation/v2` 布局(2026-07-31,用户裁定弃兼容,已实施)**。episode_meta 从 scalar_string 列改为**每 episode 一个 json blob**,与 joints_pose/subtasks 同形态、按槽位窗口按需加载——写侧 O(1)/条,整列重写的写放大就此消除;schemaType bump 到 `video.annotation/v2`,v1 残留数据在 open 时被 `_check_schema_type` 干净拒绝(用户将删除全部旧数据,不做迁移)。episode_meta **不可删除**的结论存档:它是 task/scene 标签、primary_camera、每相机 fps/编码/源文件路径(embed 依赖)、src_fps 的唯一载体,且 SDK 自身的 revise/add_cameras/embed 都读它——UI 不显示不等于无消费者。接受的 trade:全量列表从 scalar 的"一列一次读"变为每页 K 次 GET(分页 API 缓解;引擎 Fragment 重打包落地后收敛)。TS CLI `schema.ts` 同步待办。
- **episode_index 改为 scalar_string(2026-07-31,用户裁定,已实施)**。读 dreamdb 源码(spec/0011,`track.rs`)后修正认知:scalar track 是**按值倒排**的索引——Track Object 内联每个 distinct 值的字节并指向该值的 anchor 桶;物理分组按 value 而非 anchor。由此:(a) episode_meta 的 O(N) 写放大机制 = 全 distinct 的 1KB 值全部内联在 Track Object 里,追加即整体重写——它是倒排设计的最坏用例,v2 改 blob 的结论不变且更硬;(b) id 索引恰是倒排设计的**最佳用例**:30B distinct 小值内联 → 一次可缓存的 fetch 拿到完整 id→anchor 映射,写侧 O(N×30B) 平滑;(c) blob 索引被否:实测逐条提交下每条一个远端对象,`pack_items` 只在单批次内打包(design/0008),`compact()` 不重打包 Fragment track——全量读 id = N 次 GET。index 值为裸 id 字符串,无 JSON 包装;读侧容忍短暂存在过的 blob 中间格式。引擎需求追加:compact 扩展到 Fragment track 重打包(v2 meta blob 的读取收敛依赖它)。

- **扩展性 Phase 0+(2026-07-31,已实施)**。实测确认 dreamdb scalar 列追加为整列重写(边际成本 ∝ N,累计 O(N²)),blob 追加为 O(1)/条。preset 写路径去全扫:space meta 槽位注册表 `dreamdb.dataset.slots`(next_gid + 每相机几何,O(1))+ 新增 `episode_index` blob track(每 episode 一个 `{"episode_id"}` 小对象,与 meta 同行提交,additive、viewer 安全);`add_episode`/`add_cameras`/`revise`/`episode(id)` 不再读 meta 全列;新增 `episode_count()` 与分页 `episodes(after_gid=, limit=)`(槽位窗口 ranged read,O(页));旧数据集首次写入时一次性迁移。**未解决**(引擎层,已立项讨论):episode_meta scalar 列本身的写放大(需分段 scalar track / HotShard 扩展到 scalar / manifest 分段,或 v2 把 meta 改为 blob——非 additive,需与 viewer/TS 协同)。v2 方向的前端实证:dreamlake-ai 中唯一生产级的 dreamdb 浏览器读取路径(`lib/search/visionMixedSource.ts`)的范式是 blob track + `resolveObjectIndex`(对象索引给出 `[anchor, end, size, hash]`,不读内容)+ 按 anchor 的 Range 逐条读 + IDB 按 manifest hash 缓存——完全不用 scalar 列;episode_meta"单列读"所服务的 UI 列表页在生产中尚不存在。故 v2 建议:fat meta 改 blob(写 O(1)、viewer 走对象索引),`episode_index` 升级为"列表卡片"(`{episode_id, task, duration_s, cameras}`,`pack_items` 打包),列表页只读小 track,fat meta 按需 Range 读。index 内容不可为空的原因也在此记录:anchors 免费提供占位与计数,但 episode_id→slot 的身份映射必须有载体(30B/条),否则 id 寻址退回 fat 列扫描。同批:JSON 数据面写入统一 `dumps_compact`(无分隔符空白 + `ensure_ascii=False`)。
- **已实施(2026-07-31)**。实施与设计的三处偏差,均已回写本文档:(1) fields 镜像键为 `dreamdb.dataset.fields`(dreamdb `set_meta` 强制 `dreamdb.` 前缀);(2) preset 保留在 `_core.py`(类改名 `VideoAnnotationDataset` 并继承基类,不迁文件,减小回归面);(3) `t.ingest` 的无损 remux 路径直接用 dreamdb 公开的 `ingest_video`(实施时发现该方法即是 remux+`ingest_cmaf` 的封装)。测试:274 passed / 55 skipped(既有 240 全绿,新增 34 覆盖 Schema 校验、限定名、分派、镜像、Track 读写、write-once、ensure/reload/lease)。

- v3 追补 4:`t.extend` 更名 `t.append_range`——统一写动词族:`append`(特定 anchor 的点)/ `append_rows`(行式,跨 track)/ `append_range`(列式区间),动词统一为 append-only 的 append,后缀编码形状。
- v3 追补 3:`append`/`extend` 区分轴改为"点 vs 区间"(`append(anchor, value)` 向特定 anchor 追加;`extend(items)` 向时间线追加一段区间,SDK 排序、批内重复 anchor 报错);写侧章节重述为"原生表示透传 + 校验为主体,输入便利为辅";裁撤公开的 `set_meta/get_meta`(space meta 为空间级全局 KV、非 track、不随 anchor;系统内部继续使用,用户级需求出现前不占公共表面)。
- v3 追补 2:Schema 与 `dreamdb.Schema` 零转换层(同名同参;撤回短 kind 词汇与 `add_json`,JSON 即 `image`+`mime="json"` 原样外露);撤回 `Schema.from_tracks`(track 与 schema 非一一对应);`t.append(anchor, value)` 单点 + `t.extend(items)` 批量(Python list 语义);读侧收敛为"存储表示原样返回,唯 json 解码一个例外";`set_meta/get_meta` 的存储位置与用途写明(space meta / S3 侧,server 无对应端点,价值在保留前缀 guard)。
- v3 追补:删除 `ds.schema`——`ds.tracks()` 即 schema 的活形态,`Schema` 收窄为纯写侧声明容器(create/ensure 输入 + wire format)(用户决定)。
- v2 → v3:吸收第二轮方法级 review + 限定名寻址需求。ADD:`Dataset.ensure`(open-or-create)、`ds.anchors()`(计数/跨度/落库验证;dreamdb `count()` 在纯标量数据集上不可用,走 `list_anchors`)、`ds.reload()`(镜像与凭证过期的统一恢复动词)。CUT:`ds.append(anchor, values)`(等价单行 `append_rows`,且诱导逐点提交)、公开的 `Dataset.for_type`(注册表转私有)。CHANGE:`t.ingest` 补全(`height` 区分无损 remux / 重编码,init-segment 约束显式化,返回摘要,重叠预检)、`kind` 参数改短词汇并与 Schema builder 对齐、"空 track 读回 `[]`"入契约、`sequence_anchors` 定形、`DatasetInfo` 钉到 server `toSummary` 形状。新增 §3 `namespace/name` 限定名寻址(server 端实证零改动:`resolveNamespace` + `hasFullAccessForNamespace` 已支持 org 成员创建/读写)。
- v1 → v2:吸收第一轮 review。修正同 anchor 语义(内容序实证,改为 write-once,删除 `t.set`);修正 `_platform.py` "无需改动"的错误说法;新增 fields 镜像机制(dreamdb 无自省 API 实证);往返契约补 video 排除与 None 边界;Schema 增 `add_json` 糖与保留名拒绝;`Dataset.delete` 改 classmethod(避免与 `ds.db.delete` 撞名);`start_ns/end_ns` 改 `start/end`;preset `tracks()` 返回类型统一;raw view 明确为未建的独立交付物;新增并发单写者与凭证 lease 稳定性条款;范围调整:SDK/CLI source 代码暂不动,统一延后清理。
