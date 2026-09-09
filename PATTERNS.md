# 设计约定

## 平铺脚本与判分复用

Python 文件保存在仓库根目录，直接 import。evaluate.py 调用 grade.py 的判分函数与缓存，评分公式维护在 grade.py。

## 题目、工作目录与会话

ordinal 从 1 连续编号，对外使用题序号。题目顺序来自 gitignored 的 data/task_ids.txt。题目签名覆盖 Task 字段；题根目录 manifest.json 保存其与输入摘要的对应关系。每个产品、每题使用独立会话和题目录，人工 CSV 按 product + task 匹配，同一产品的会话值保持唯一。

原始输入放在题面的 prompt.txt 与 reference_files 中；最终答案集中在 output。校验输入摘要后才将答案送 judge。每个产品有独立的附件副本。备题保留已有人工记录与交付物，已存在的输入必须与原始材料一致。

题包附件直接使用 reference_files/原始文件名。下载缓存按 URL 摘要隔离；题包平铺前检查忽略大小写的同名冲突。重复备题将已记录的嵌套附件平铺，核验内容一致后更新映射和清理空目录，保留人工记录与交付物。

## 状态和空值

人工状态使用 pending、running、completed、failed。completed 收取最终答案后进入判分，成功记 graded；输入或 judge 错误记 error。人工 failed 计零分，其余未完成与错误留空。只有整套题均 graded 或 failed 的产品可参与排名。

耗时为有限非负秒数，token 为非负整数。空值为未知，0 为已确认零消耗。汇总同时给出填写覆盖数，全部填写才显示该指标总计。

## judge 和缓存

judge 单次请求包含当前题的材料，末行必须准确为 MET / NOT_MET，或 A / B / TIE。畸形回复抛出 JudgeError，评分管线标记错误供重跑。

pairwise 在每题用题目签名初始化随机数，将产品答案随机放 A/B；把位置判断换算为产品得分，缓存保存 a_side。同一题重跑和各产品使用一致的位置规则。

每产品缓存为 JSONL，逐条追加，支持中断续跑。key 覆盖题目签名、model、provider、judge 系统提示、参考材料、答案原始字节摘要和提取文本；pairwise 另包含专家答案文本。答案变化或模型变化时重新判分。专家答案按题目签名隔离本地缓存。

## 文件和输出

下载使用 HTTPS，先写 .part，完整下载后替换目标；网络错误最多尝试三次。题包文件路径使用目录内的相对路径，校验路径穿越和符号链接。

判分异常显示题序号与固定原因，底层异常只输出类型。题包、题目选择、判定缓存与测评报告放在 gitignored 目录。

XLSX、DOCX、PPTX、PDF 提取为文本；参考 TXT 以 UTF-8 读取。DOCX 从 document.xml 提取正文与表格。单份/单组文本上限 2,000,000 字符，Excel 上限 200,000 单元格，PDF 上限 500 页；管线检测空文本和超限，报错交人工核对。视觉和公式运行结果由人工验收。

文件枚举略过 .DS_Store、Thumbs.db、AppleDouble 元数据和 Office 临时锁文件，最终交付物仍按全部有效文件一起判分。
