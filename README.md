# POTI 小松鼠 · 商业数学模型

英国学生起步、毕业后延续使用的个人财务管理 App。此仓库为 Innovation Management pitch 提供可复算的月度及三年财务草案。金额为 GBP，情景输出取决于假设及所列成本范围。

| 文件 | 内容 |
|---|---|
| [assumptions.md](assumptions.md) | 证据、状态、历史口径及缺口 |
| [parameters.json](parameters.json) | 当前输入、待补字段及情景覆盖值 |
| [model.py](model.py) | Python 月度计算及年度汇总 |
| [scenarios.md](scenarios.md) | 情景结果与差异 |
| [calculations.md](calculations.md) | 公式、边界、课程手册与验证说明 |

使用 Python 3，无第三方依赖。

```sh
python3 model.py
```

运行后生成 `results/monthly.csv` 与 `results/annual.csv`。前者可直接画月收入、部分现金成本和累计差额，后者用于年度汇总。空单元格表示缺少输入。可以用 `--parameters` 指向新的 JSON，用 `--out` 指定输出目录。

更新假设时同步记录单位、分母、来源和证据状态，再重跑结果。当前适合展示驱动因素与敏感性。完整成本、融资额与真实留存尚未验证。
