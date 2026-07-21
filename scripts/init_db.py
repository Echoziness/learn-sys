#!/usr/bin/env python3
"""初始化知识库：建表 + FTS5 + sqlite-vec + 种子条目 + test1 用户画像。"""

import json, os, sqlite3, struct, sys

import sqlite_vec
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

load_dotenv()

# ---- 路径 ----
DB_PATH = os.getenv("DATABASE_PATH", "data/knowledge.db")
BGE_PATH = os.getenv("BGE_MODEL_PATH", "data/bge-m3")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(BGE_PATH, exist_ok=True)

# ---- 建表 ----
db = sqlite3.connect(DB_PATH)
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA foreign_keys=ON")
db.enable_load_extension(True)
sqlite_vec.load(db)

db.executescript("""
    -- 知识条目（通用模板）
    CREATE TABLE IF NOT EXISTS knowledge_entries (
        id          TEXT PRIMARY KEY,
        domain      TEXT NOT NULL DEFAULT 'bigdata-analysis',
        title       TEXT NOT NULL,
        content     TEXT NOT NULL,
        prerequisites TEXT,          -- JSON array of entry IDs
        difficulty  INTEGER CHECK(difficulty BETWEEN 1 AND 5),
        keywords    TEXT,            -- JSON array
        source      TEXT,
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
    );

    -- 全文检索
    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
        title, content, keywords,
        content='knowledge_entries', content_rowid='rowid'
    );

    -- 向量检索
    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec USING vec0(
        embedding FLOAT[1024]
    );

    -- 学习者
    CREATE TABLE IF NOT EXISTS learners (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        created_at  TEXT DEFAULT (datetime('now'))
    );

    -- 学习者画像
    CREATE TABLE IF NOT EXISTS learner_profiles (
        learner_id  TEXT PRIMARY KEY REFERENCES learners(id),
        background  TEXT NOT NULL,   -- JSON
        mastery     TEXT,            -- JSON: {entry_id: 0.0-1.0}
        style_tags  TEXT,            -- JSON
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
    );

    -- 画像变更记录
    CREATE TABLE IF NOT EXISTS profile_updates (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        learner_id  TEXT NOT NULL REFERENCES learners(id),
        source      TEXT NOT NULL,   -- initial_test / assessment / feedback
        detail      TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    );

    -- 编排执行记录
    CREATE TABLE IF NOT EXISTS run_history (
        id            TEXT PRIMARY KEY,
        learner_id    TEXT NOT NULL REFERENCES learners(id),
        status        TEXT DEFAULT 'pending',
        start_at      TEXT,
        end_at        TEXT,
        state_snapshot TEXT,
        created_at    TEXT DEFAULT (datetime('now'))
    );

    -- 生成资源
    CREATE TABLE IF NOT EXISTS generated_resources (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        TEXT NOT NULL REFERENCES run_history(id),
        learner_id    TEXT NOT NULL REFERENCES learners(id),
        resource_type TEXT NOT NULL, -- lecture / guide / quiz
        content       TEXT NOT NULL, -- JSON
        evidence_ids  TEXT,          -- JSON
        review_verdict TEXT,
        created_at    TEXT DEFAULT (datetime('now'))
    );

    -- 对话日志（供画像更新读取，禁进生成上下文）
    CREATE TABLE IF NOT EXISTS conversation_logs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        learner_id  TEXT NOT NULL REFERENCES learners(id),
        source      TEXT NOT NULL,   -- user / agent-diagnose / agent-generate / agent-review
        content     TEXT NOT NULL,
        run_id      TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    );

    -- 测试结果
    CREATE TABLE IF NOT EXISTS assessment_results (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        learner_id  TEXT NOT NULL REFERENCES learners(id),
        run_id      TEXT REFERENCES run_history(id),
        answers     TEXT NOT NULL,   -- JSON
        score       REAL,
        created_at  TEXT DEFAULT (datetime('now'))
    );
""")
print("[1/5] 表结构创建完成")

# ---- 种子条目 ----
SEED_ENTRIES = [
    {
        "id": "BDA-DB-001",
        "title": "关系型数据库基本概念",
        "content": "关系型数据库以表（Table）为基本存储单元，表由行（Row/Record）和列（Column/Field）组成。每行代表一条记录，每列代表一个属性。主键（Primary Key）唯一标识一行，外键（Foreign Key）建立表之间的引用关系，确保数据的参照完整性。常见的关系型数据库包括 SQLite、PostgreSQL、MySQL。",
        "prerequisites": "[]",
        "difficulty": 1,
        "keywords": "[\"数据库\", \"关系型\", \"表\", \"主键\", \"外键\"]",
        "source": "ISO/IEC 9075 SQL 标准 / 数据库系统概论"
    },
    {
        "id": "BDA-SQL-001",
        "title": "SELECT 基础查询",
        "content": "SELECT 语句用于从数据库表中检索数据。基本语法为 SELECT 列名 FROM 表名。可以使用 * 选取所有列，使用 DISTINCT 去除重复行。SELECT 配合 FROM 构成 SQL 查询的最简形式，返回符合条件的数据集（Result Set）。",
        "prerequisites": "[\"BDA-DB-001\"]",
        "difficulty": 2,
        "keywords": "[\"SQL\", \"SELECT\", \"FROM\", \"查询\"]",
        "source": "SQL Standard ISO/IEC 9075 / SQLite 官方文档"
    },
    {
        "id": "BDA-SQL-002",
        "title": "WHERE 条件过滤",
        "content": "WHERE 子句用于对 SELECT 查询结果进行行级过滤，只返回满足指定条件的行。条件可使用比较运算符（=, <>, <, >, <=, >=）、逻辑运算符（AND, OR, NOT）、范围判断（BETWEEN）、集合匹配（IN）、模式匹配（LIKE）以及空值判断（IS NULL / IS NOT NULL）。多个条件用 AND/OR 组合时，建议使用括号明确优先级。",
        "prerequisites": "[\"BDA-SQL-001\"]",
        "difficulty": 2,
        "keywords": "[\"SQL\", \"WHERE\", \"过滤\", \"条件\"]",
        "source": "SQL Standard ISO/IEC 9075"
    },
    {
        "id": "BDA-SQL-003",
        "title": "ORDER BY 与 LIMIT",
        "content": "ORDER BY 子句对查询结果按指定列进行排序，支持 ASC（升序，默认）和 DESC（降序）。LIMIT 子句限制返回的行数，常配合 OFFSET 实现分页查询。排序可指定多个列，按书写顺序依次比较，列名后可独立控制升降序。",
        "prerequisites": "[\"BDA-SQL-001\"]",
        "difficulty": 2,
        "keywords": "[\"SQL\", \"ORDER BY\", \"排序\", \"LIMIT\", \"分页\"]",
        "source": "SQL Standard ISO/IEC 9075"
    },
    {
        "id": "BDA-SQL-004",
        "title": "聚合函数与 GROUP BY",
        "content": "聚合函数对一组行执行计算并返回单一值，常用函数包括 COUNT（计数）、SUM（求和）、AVG（均值）、MAX（最大值）、MIN（最小值）。GROUP BY 子句将数据按指定列分组，配合聚合函数对每组独立计算。HAVING 子句用于对分组结果进行过滤（WHERE 过滤行，HAVING 过滤组）。聚合函数默认忽略 NULL 值，COUNT(*) 例外。",
        "prerequisites": "[\"BDA-SQL-001\"]",
        "difficulty": 3,
        "keywords": "[\"SQL\", \"GROUP BY\", \"聚合\", \"COUNT\", \"SUM\", \"AVG\", \"HAVING\"]",
        "source": "SQL Standard ISO/IEC 9075"
    },
    {
        "id": "BDA-SQL-005",
        "title": "JOIN 表连接",
        "content": "JOIN 操作用于根据两个表之间的相关列组合行。INNER JOIN 返回两表匹配的行；LEFT JOIN 保留左表全部行，右表无匹配时填充 NULL；RIGHT JOIN 反之；FULL OUTER JOIN 保留两表全部行。CROSS JOIN 返回笛卡尔积。连接条件写在 ON 子句中，USING 可在列名相同时简化写法。多表连接时注意连接顺序不影响结果但影响性能。",
        "prerequisites": "[\"BDA-SQL-001\", \"BDA-DB-001\"]",
        "difficulty": 4,
        "keywords": "[\"SQL\", \"JOIN\", \"INNER JOIN\", \"LEFT JOIN\", \"连接\"]",
        "source": "SQL Standard ISO/IEC 9075"
    },
    {
        "id": "BDA-DT-001",
        "title": "数据预处理概述",
        "content": "数据预处理是数据分析流程中位于原始数据采集之后、建模分析之前的关键步骤，包括数据清洗（处理缺失值、异常值、重复值）、数据变换（标准化、归一化、离散化）、数据规约（降维、抽样）和数据集成（多源合并）。高质量预处理能显著提升后续分析的准确性和可靠性，是实际数据分析项目中耗时最长（通常占 60-80%）的环节。",
        "prerequisites": "[]",
        "difficulty": 2,
        "keywords": "[\"数据清洗\", \"缺失值\", \"标准化\", \"预处理\"]",
        "source": "数据挖掘：概念与技术 / Python for Data Analysis"
    },
    {
        "id": "BDA-DT-002",
        "title": "缺失值处理方法",
        "content": "处理缺失值有三种主要策略：删除（移除含缺失值的行或列，适用于缺失占比很小的情况）、填充（使用均值/中位数/众数填充数值列，使用众数或'未知'填充分类列）、插值（利用前后数据点估算缺失值，适用于时间序列）。选择策略取决于缺失的原因（MCAR/MAR/MNAR）、缺失比例以及分析目标。无万能的默认策略，需结合业务上下文判断。",
        "prerequisites": "[\"BDA-DT-001\"]",
        "difficulty": 3,
        "keywords": "[\"缺失值\", \"数据清洗\", \"填充\", \"插值\"]",
        "source": "Python for Data Analysis / pandas 官方文档"
    },
    {
        "id": "BDA-PY-001",
        "title": "pandas DataFrame 基础",
        "content": "DataFrame 是 pandas 核心的二维表格型数据结构，由行索引（Index）和列名（Columns）组织数据。可通过 pd.read_csv() 从 CSV 文件读取，pd.DataFrame() 从字典或列表构造。常用操作包括 df.head() 预览前几行、df.info() 查看列类型和非空计数、df.describe() 生成数值列的描述性统计、df['列名'] 选取列、df.loc[] 按标签索引、df.iloc[] 按位置索引。",
        "prerequisites": "[]",
        "difficulty": 2,
        "keywords": "[\"pandas\", \"DataFrame\", \"Python\", \"数据处理\"]",
        "source": "pandas 官方文档"
    },
    {
        "id": "BDA-PY-002",
        "title": "pandas 数据过滤与排序",
        "content": "在 pandas 中可通过布尔索引过滤数据：df[df['列名'] > 值] 返回符合条件的行，多个条件用 &（与）|（或）~（非）组合，每个条件需用括号包裹。df.sort_values('列名') 按列排序，ascending=False 降序。df.query('列名 > 值') 提供更简洁的字符串查询语法。df.drop_duplicates() 去除重复行，subset 参数指定去重判定的列。",
        "prerequisites": "[\"BDA-PY-001\"]",
        "difficulty": 3,
        "keywords": "[\"pandas\", \"过滤\", \"排序\", \"布尔索引\"]",
        "source": "pandas 官方文档"
    },
    {
        "id": "BDA-PY-003",
        "title": "pandas 分组聚合",
        "content": "df.groupby('列名') 按指定列分组，返回 GroupBy 对象。对 GroupBy 对象调用 .sum() / .mean() / .count() / .agg() 等方法进行分组计算。.agg() 可同时对多列应用不同聚合函数：df.groupby('A').agg({'B': 'sum', 'C': 'mean'})。.transform() 返回与原始 DataFrame 等长的结果，适用于组内标准化等操作。分组后可接 .reset_index() 将分组键还原为普通列。",
        "prerequisites": "[\"BDA-PY-001\"]",
        "difficulty": 3,
        "keywords": "[\"pandas\", \"groupby\", \"分组\", \"聚合\"]",
        "source": "pandas 官方文档"
    },
    {
        "id": "BDA-ST-001",
        "title": "描述性统计",
        "content": "描述性统计通过数值指标概括数据特征。集中趋势指标：均值（算术平均，对异常值敏感）、中位数（排序后中间值，稳健）、众数（出现频率最高的值）。离散程度指标：方差（与均值的平方偏差均值）、标准差（方差的平方根，量纲与数据一致）、极差（最大值减最小值）、四分位距 IQR（Q3-Q1，描述中间 50% 数据的范围）。偏度描述分布的对称性，峰度描述分布的尾部厚度。",
        "prerequisites": "[]",
        "difficulty": 2,
        "keywords": "[\"统计\", \"均值\", \"中位数\", \"标准差\", \"方差\"]",
        "source": "概率论与数理统计 / OpenIntro Statistics"
    },
    {
        "id": "BDA-ST-002",
        "title": "相关性分析",
        "content": "相关性衡量两个变量之间的线性关联程度。皮尔逊相关系数 r 取值 [-1, 1]：r>0 正相关（一增一增），r<0 负相关（一增一减），r≈0 无线性相关。|r|>0.7 视为强相关，0.3-0.7 中等，<0.3 弱相关。重要陷阱：相关性不是因果性——两变量即使高度相关，也可能由第三个混杂变量同时驱动。斯皮尔曼等级相关系数基于秩次，适用于非线性单调关系。",
        "prerequisites": "[\"BDA-ST-001\"]",
        "difficulty": 3,
        "keywords": "[\"统计\", \"相关性\", \"皮尔逊\", \"相关系数\", \"因果性\"]",
        "source": "OpenIntro Statistics / 统计学"
    },
    {
        "id": "BDA-VIS-001",
        "title": "数据可视化基本原则",
        "content": "数据可视化通过图形映射数据维度，降低认知门槛。核心原则：图表类型由数据特征和探索目标决定——比较类别用柱状图，展示分布用直方图或箱线图，观察趋势用折线图，分析关系用散点图，呈现构成用饼图或堆叠图。避免 3D 效果和过度装饰（Chart Junk），坐标轴应从 0 起始（柱状图必须，折线图可视情况调整），用颜色区分而非仅靠图例文字。一张图回答一个核心问题。",
        "prerequisites": "[]",
        "difficulty": 2,
        "keywords": "[\"可视化\", \"图表\", \"柱状图\", \"散点图\", \"折线图\"]",
        "source": "The Visual Display of Quantitative Information (Tufte) / Storytelling with Data"
    },
    {
        "id": "BDA-VIS-002",
        "title": "Matplotlib 与 Seaborn 基础",
        "content": "Matplotlib 是 Python 最基础的绑图库，通过 plt.plot() / plt.bar() / plt.scatter() 等函数绑定。Seaborn 在 Matplotlib 之上提供更高级的统计图形接口：sns.histplot() 直方图、sns.boxplot() 箱线图、sns.scatterplot() 散点图、sns.heatmap() 热力图。Seaborn 自动处理美观的默认样式和颜色方案，并与 pandas DataFrame 原生集成。两者可混用——Seaborn 画高层图，Matplotlib 调细节（坐标轴标签、标题、图例位置）。",
        "prerequisites": "[\"BDA-VIS-001\", \"BDA-PY-001\"]",
        "difficulty": 3,
        "keywords": "[\"可视化\", \"Matplotlib\", \"Seaborn\", \"Python\"]",
        "source": "Matplotlib 官方文档 / Seaborn 官方文档"
    },
]

for entry in SEED_ENTRIES:
    db.execute(
        """INSERT OR IGNORE INTO knowledge_entries
           (id, domain, title, content, prerequisites, difficulty, keywords, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (entry["id"], "bigdata-analysis", entry["title"], entry["content"],
         entry["prerequisites"], entry["difficulty"], entry["keywords"], entry["source"])
    )

# 同步 FTS5
db.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES ('rebuild')")

print(f"[2/5] 种子条目 {len(SEED_ENTRIES)} 条写入完成")

# ---- BGE-M3 embedding ----
print("[3/5] 加载 BGE-M3（首次运行需下载 ~2GB，约 5-10 分钟）...")
model = SentenceTransformer("BAAI/bge-m3", cache_folder=BGE_PATH)
contents = [e["content"] for e in SEED_ENTRIES]
embeddings = model.encode(contents, normalize_embeddings=True, show_progress_bar=True)

for rowid, emb in enumerate(embeddings, start=1):
    db.execute(
        "INSERT INTO knowledge_vec(rowid, embedding) VALUES (?, ?)",
        (rowid, struct.pack("1024f", *emb))
    )
print(f"[3/5] embedding 完成（{len(embeddings)} 条，维度 {embeddings.shape[1]}）")

# ---- test1 用户 ----
db.execute("INSERT OR IGNORE INTO learners(id, name) VALUES ('test1', '测试学员-转行数据分析')")
db.execute("""
    INSERT OR REPLACE INTO learner_profiles(learner_id, background, mastery, style_tags)
    VALUES ('test1', ?, ?, ?)
""", (
    json.dumps({
        "education": "本科大二",
        "major": "机械工程",
        "goal": "转行数据分析",
        "experience": "学过 C 语言基础，会用 Excel 做简单统计，未接触过数据库和 Python"
    }, ensure_ascii=False),
    json.dumps({}, ensure_ascii=False),  # 初始无掌握度，首次诊断时建立
    json.dumps(["code-first"], ensure_ascii=False)
))
db.execute("""
    INSERT OR IGNORE INTO profile_updates(learner_id, source, detail)
    VALUES ('test1', 'initial_test', ?)
""", (json.dumps({"method": "manual", "note": "初始画像，尚未进行知识诊断"}, ensure_ascii=False),))

print("[4/5] test1 用户创建完成")

# ---- 验证 ----
db.commit()

count_entries = db.execute("SELECT count(*) FROM knowledge_entries").fetchone()[0]
count_vec = db.execute("SELECT count(*) FROM knowledge_vec").fetchone()[0]
count_fts = db.execute("SELECT count(*) FROM knowledge_fts").fetchone()[0]
profile = db.execute("SELECT background FROM learner_profiles WHERE learner_id='test1'").fetchone()

print(f"[5/5] 验证通过: {count_entries} 条条目, {count_vec} 条向量, {count_fts} 条 FTS 索引")
print(f"      test1 画像: {json.loads(profile[0])['goal']}")
print(f"      数据库: {DB_PATH}")
db.close()
print("\n✅ 初始化完成")
