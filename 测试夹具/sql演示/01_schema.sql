-- 由「拆表原型.py」从 runs/20261011_001/raw.json 生成（提案原型，非插件现有输出）
PRAGMA foreign_keys = ON;

CREATE TABLE probe (
  probe_id   INTEGER PRIMARY KEY,
  run_id     TEXT NOT NULL,
  image_id   TEXT NOT NULL,
  row_id     TEXT NOT NULL,            -- 溯源到 raw.json 的行号
  sheet      TEXT NOT NULL,
  object_label TEXT,
  has_uncertain INTEGER DEFAULT 0
);

-- stock_snapshot（库存明细快照）：一行 = 一个库位在一个时点
CREATE TABLE stock_snapshot (
  snapshot_id INTEGER PRIMARY KEY,

  probe_id INTEGER REFERENCES probe(probe_id),

  "库位" TEXT,

  "储存区域" TEXT,

  "编号" TEXT,

  "物料名称" TEXT,

  "来源" TEXT,

  "状态" TEXT,

  "规格" TEXT,

  "性质" TEXT,

  component_text TEXT          -- 成分明细原文，保真
);

-- tank_snapshot（储位盘点快照）：一行 = 一个储位在一个时点
CREATE TABLE tank_snapshot (
  snapshot_id INTEGER PRIMARY KEY,

  probe_id INTEGER REFERENCES probe(probe_id),

  "储存区域" TEXT,

  "物料名称" TEXT,

  "来源" TEXT,

  "状态" TEXT,

  "性质" TEXT,

  component_text TEXT          -- 成分明细原文，保真
);

-- feed_log（进料记录）：一行 = 一个库位在一个时点
CREATE TABLE feed_log (
  snapshot_id INTEGER PRIMARY KEY,

  probe_id INTEGER REFERENCES probe(probe_id),

  "日期" TEXT,

  "塔号" TEXT,

  "原料名称" TEXT,

  "数量" TEXT,

  "进料时间" TEXT,

  "原料贮存位置" TEXT,

  "备注" TEXT,

  component_text TEXT          -- 成分明细原文，保真
);

-- incoming_batch（来料批次行）：一行 = 一个库位在一个时点
CREATE TABLE incoming_batch (
  snapshot_id INTEGER PRIMARY KEY,

  probe_id INTEGER REFERENCES probe(probe_id),

  "序号" TEXT,

  "物料名称" TEXT,

  "入库圈号" TEXT,

  "化验结果" TEXT,

  "入罐号或并箱去处" TEXT,

  component_text TEXT          -- 成分明细原文，保真
);

-- 长表（度量）：一行 = 一个对象 × 一个指标 × 一个值
--   正是它让「液位/密度/使用率」这类数值从来料总表里被拆出来，能直接 SQL 查
CREATE TABLE measure (
  snapshot_id INTEGER NOT NULL,
  sheet       TEXT NOT NULL,
  metric      TEXT NOT NULL,
  value_num   REAL,
  unit        TEXT,
  raw_value   TEXT            -- 图上原文，保真
);

-- 成分表：一行 = 一个对象 × 一个组分（从"成分明细"自由文本里拆出来）
CREATE TABLE component_reading (
  snapshot_id INTEGER NOT NULL,
  sheet       TEXT NOT NULL,
  component   TEXT,
  value_num   REAL,
  unit        TEXT,
  raw_token   TEXT,           -- 拆之前那个词的原样
  category    TEXT            -- main / water / misc / ph / note
);

-- 箱号表：一个批次行 × 一个箱号（把"小桶货方箱编号"那一格长串拆成行）
CREATE TABLE incoming_box (
  snapshot_id INTEGER NOT NULL,
  box_no      TEXT NOT NULL,
  sheet       TEXT NOT NULL,
  material    TEXT,
  assay       TEXT,
  destination TEXT
);

-- 问题表：账本进关系库，一个决定都不丢
CREATE TABLE issue (
  issue_id    TEXT PRIMARY KEY,
  type        TEXT, severity TEXT, sheet TEXT, object TEXT, field TEXT,
  description TEXT, target_ids TEXT, resolved INTEGER DEFAULT 0
);
