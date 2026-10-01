# Foursquare Tokyo Department Store Cross-Recommendation

这是 Foursquare Tokyo Department Store 实验的独立复现包。项目从官方
TSMC2014 Tokyo 数据构造时间图，在同一个 User--Venue 与 Venue--Venue 混合图上
计算逐边的精确 C3--C6 support 和 trussness，并比较五种方法：

- ItemKNN
- C3-truss only
- C4-truss only
- C5-truss only
- C6-truss only

公开结果只报告 Precision@5 和 binary NDCG@5。每种方法都对同一个完整的
warm-unseen Item 集合排序。主分数为 0 的 Item 也保留，因此每位测试用户都会
获得 5 个推荐。统一排序规则为：

```text
方法主分数降序
训练混合图中的 Item degree 降序
Item ID 升序
```

Item degree 是该 Item 在训练图中关联的 User--Item 边和 Item--Item 边总数，
测试边从不参与 degree 计算。

## 直接运行

需要 Python 3.10+、C++17 编译器，以及 `curl` 或 `wget`。不需要 sudo、pip
或图数据库服务器。

```bash
cd "Case- Foursquare-Tokyo"
bash scripts/run_foursquare.sh
```

该命令把新运行写入 `results/reproduction`；已核验的内置结果保留在
`results/run`。

查看保存的结果或运行测试：

```bash
bash scripts/show_results.sh
bash scripts/run_tests.sh
```

## 正式结果

| Method | NDCG@5 | Precision@5 |
|---|---:|---:|
| ItemKNN | 0.1342 | 0.0633 |
| C3-truss only | 0.2171 | **0.0918** |
| C4-truss only | 0.2192 | 0.0878 |
| **C5-truss only** | **0.2243** | **0.0918** |
| C6-truss only | 0.2067 | 0.0878 |

C5-truss 的 NDCG@5 最高，Precision@5 与 C3-truss 并列最高。

## 数据 pipeline

### 1. 原始数据

脚本从官方地址下载 TSMC2014 数据：

```text
https://www-public.imtbs-tsp.eu/~zhang_da/pub/dataset_tsmc2014.zip
```

程序会检查 Tokyo 文件的字段以及 573,703 条原始到访记录。

### 2. 时间切分

固定时间点为：

```text
2012-12-01 00:00:00 UTC
```

- 时间点之前不同的 User--Venue 到访关系形成训练边；重复到访合并成一条边。
- 某个关系第一次出现在时间点之后，而且 User 和 Venue 都已出现在训练图中时，
  它才作为测试正例。
- 训练图中已经存在的 User--Venue 边不会再次作为测试正例。

因此，训练边与测试边没有重合。

### 3. Item 和 Item--Item 边

- Item 是训练期 category 精确为 `Department Store` 的 Tokyo Venue。
- Venue 的 category 和坐标由时间点之前最早的一条记录固定。
- 两个 Venue 的地理距离不超过 2 km 时，加入无向 Item--Item 边。
- 保留所有满足距离条件的边，不使用 top-k 稀疏化。

### 4. 训练混合图

```text
User vertices                         916
Venue/Item vertices                   172
Training User--Venue edges          1,679
Training Venue--Venue edges           765
Total training edges                2,444
Test users                              98
Future positive User--Venue edges     160
Scored User--Venue pairs           16,519
```

同分排序所用的 Item degree 只由训练图计算：

```text
degree(i) = incident training User--Item edges
          + incident training Item--Item edges
```

### 5. 精确 cycle decomposition

C++ 后端在同一个无向训练混合图上枚举 generic simple C3、C4、C5 和 C6，
并执行精确的 edge peeling。support 和 trussness 都赋在 edge 上，不是 node 上；
算法也不会临时加入待预测的 User--Item 边后再计算 decomposition。

### 6. 五种推荐分数

- `itemknn`：训练 User--Item 图上的 cosine ItemKNN。
- `c3_truss_only`：对训练期未连接的 `(u,c)`，寻找训练路径 `u--b--c`，其中
  `u--b` 是 User--Item 边，`b--c` 是 Item--Item 边；分数取相应 `b--c`
  边中最大的 C3-trussness。
- `c4_truss_only`：使用 typed simple C4 `U-I-I-I`。对于
  `u-a-b-c-u`，其中 `u-b` 是待预测关系，一个环的分数为 `a-b` 和 `b-c`
  两条 Item--Item 边中较大的 C4-trussness；有多个支持环时再取最大值。
- `c5_truss_only`：使用 typed simple C5 `U-I-U-I-I`，分数取对应
  Item--Item role edge 中最大的 C5-trussness。
- `c6_truss_only`：使用 typed simple C6 `U-I-I-U-I-I`，分数取对应
  Item--Item role edge 中最大的 C6-trussness。

generic cycles 用于计算 edge trussness；typed patterns 只在下游推荐打分时使用。
如果一种方法没有为某个 Item 产生正分，该 Item 仍保留在完整目录中，主分数为 0。

### 7. 排序与评估

对 98 位测试用户中的每一位，五种方法都给训练期未访问过的全部 warm Venue
打分，再按统一规则返回恰好 5 个 Venue。Precision@5 是该用户五个推荐中未来
正例的数量除以 5，然后在全部 98 位用户上取平均。binary NDCG@5 会让排在
更前面的未来正例获得更高分；它先按每位用户的理想结果归一化，再在相同的
98 位用户上取平均。

## 输出文件

下面是内置核验结果的路径；新运行的对应文件位于 `results/reproduction`：

```text
results/run/HEADLINE_METRICS.csv
results/run/METRICS.csv
results/run/PER_USER_METRICS.csv
results/run/AUDIT.json
results/run/decomposition/MIXED_GRAPH_EDGES.tsv
results/run/decomposition/EDGE_TRUSSNESS.csv
results/run/decomposition/CYCLE_COUNTS.csv
results/run/graphdb/users.csv
results/run/graphdb/items.csv
results/run/graphdb/interactions.csv
results/run/graphdb/item_relations.csv
results/run/graphdb/cross_recommendations.csv
results/run/graphdb/queries.cypher
```

`EDGE_TRUSSNESS.csv` 按训练图 edge 保存精确的 C3--C6 support 和 trussness。
主结果 `HEADLINE_METRICS.csv` 只有三列：

```text
method,ndcg_at_5,precision_at_5
```

## 使用边界

- Venue 到访不等于电商购买。
- Department Store、2 km、时间切分点和 degree tie-break 是回顾性 case study
  的固定设置，不应描述成预注册实验。
- degree tie-break 对所有方法相同且只使用训练数据，但会决定零分 Item 的排序，
  因此不是无关紧要的显示规则。
- C5-truss 的 NDCG@5 第一，Precision@5 并列第一；不能声称它在两个指标上都
  严格超过其他方法。
