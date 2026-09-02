# PATTERNS.md

## 架构原则

- **纯函数优先**:计分、哈希、通道分派、manifest 构建都是纯函数,IO(下载、读写文件)集中在 dataset.py 与 CLI 层。
- **src layout**:业务代码在 `src/gdpval_eval/`,CLI 薄壳在 `scripts/`,只做参数解析与组装,不含业务逻辑。
- **判分正确性高于一切**:判分相关模块(scoring、hashing、manifest)的改动走盲测分离;宁可记「无证据」也不把读不到的内容判成未达标。

## 数据建模

- 共享类型集中在 `models.py`,用 frozen dataclass 与 `enum.Enum`;模块间以类型传递,不传裸 dict(manifest 的最终 JSON 序列化除外)。
- 评分点身份 = 上游 `rubric_item_id`;内容哈希只作漂移探测。按位置索引评分点的写法都是错的。
- 哈希前像:criterion 原始字符串逐码位 UTF-8,不 strip、不 unicode/换行归一(上游存在尾随空格、CR/LF、弯引号,任何顺手清理都会让冻结指纹永久对不上)。
- 计分与分值全用 `int` / `fractions.Fraction`,不引入浮点;展示层才做舍入(两位,ROUND_HALF_UP)。
- 规范化 JSON(唯一指纹前像):`json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))` 编 UTF-8,无尾随换行。

## 错误处理

- 每个模块定义自己的异常类型,继承自 `GdpvalEvalError`(models.py):`DatasetDownloadError`、`DatasetSchemaError`、`RubricParseError`、`ExamSpecError`、`ManifestCryptoError`、`ManifestFreezeError` 等。
- 异常消息、日志、CLI 输出不携带 criterion 文本、prompt、gold 内容、task_id、rubric_item_id、内容哈希——公开数据集上哈希可反查,与明文同级。定位评分点用题序号 + 题内序号。
- CLI 以退出码表意:0 通过,1 校验失败,2 环境/参数错误。

## 测试组织

- `tests/` 平铺常规单元测试(`test_<module>.py`);判分模块另有 `tests/visible/`(实现者可见)与 `tests/hidden/`(仅评审与 CI 可见语义,实现者只看计数)。
- 测试函数命名 `test_<unit>_<scenario>`,便于 `-k <unit>` 过滤。
- fixture 一律虚构:合成 criterion 文本、假 task_id(如 `task-0001`)、pyarrow 现场生成的小 parquet。真实数据只出现在标记 `integration` 的测试里,默认不跑(见 DEVFLOW.md)。

## 刻意省略的设计

- 不做判定结果存储层(唯一键、幂等写入)——那是 W2 的一部分,接口设计以 manifest 的哈希为锚点。
- 不做配置系统:两个环境变量(HF_ENDPOINT、GDPVAL_MANIFEST_KEY)+ CLI 参数足够。
