# 工具协议

使用当前包的 `.venv/bin/python` 运行 `scripts/bookkeeping.py`。一次调用只接收一个标准输入 JSON，不接受业务命令行参数。用宿主的结构化执行工具传递 stdin，或用带单引号分隔符的 heredoc；不要把用户文本拼接成可执行 shell。

```sh
"/当前技能目录/.venv/bin/python" "/当前技能目录/scripts/bookkeeping.py" <<'REQUEST'
{"tool":"session_status","host":"codex","session_id":"当前聊天的独立ID","args":{}}
REQUEST
```

请求顶层只有 `tool`、`host`、`session_id`、`args`；除 `doctor` 外均须传当前会话标识。回执为 `{"ok":true,"mode":"live|isolated","data":...}` 或 `{"ok":false,"reason":"具体错误"}`。未知工具、未知参数与越界参数会被拒绝。

| tool | args | 用途 |
|---|---|---|
| `session_status` | `{}` | 读取本会话激活状态 |
| `activate_session` | `{"user_instruction":"当前用户直接发出的完整唤醒指令"}` | 校验两种唤醒方式并开启 |
| `deactivate_session` | `{}` | 用户要求停止后关闭，并结束未完成任务 |
| `knowledge` | `{}` | 激活后读取完整合法路径和个人规则 |
| `start_task` | `{"message":"完整原始消息","request_id":"本条用户请求稳定ID"}` | 冻结原文、执行日；返回 `task_id` |
| `query_history` | `{"task_id":"...","keyword":"消费详情关键词","limit":20}` | 固定账本的有限分类样本；limit 1～50，关键词最长100字符 |
| `add_expenses` | `{"task_id":"...","expenses":[...]}` | 最多100笔；每笔独立校验和提交 |
| `finish_task` | `{"task_id":"...","decision":{"skipped":[],"no_consumption":false}}` | 根据持久回执生成最终结果 |
| `show_result` | `{"task_id":"..."}` | 读取本会话任务已有结果，退出后也可读取 |
| `doctor` | `{"live":false}` | 只检查本地资料；安装时使用 |

`doctor` 的 `live=true` 仅由安装或明确排障流程用于只读字段检查，不用于消费业务决策。首次操作真实账本前应完成此检查；失败就停止真实提交，不擅自修复认证或改字段。

`expenses` 每项参数如下，不能另加字段：

```json
{
  "source_text": "该笔消费在完整原消息中的准确连续原文",
  "source_occurrence": 1,
  "description": "source_text内足以识别消费的连续子串",
  "amount": "18",
  "amount_evidence": "source_text内准确金额依据",
  "amount_calculation": null,
  "expense_date": null,
  "date_evidence": null,
  "category_l1": "合法一级分类",
  "category_l2": "合法二级分类",
  "category_l3": null,
  "currency": "CNY",
  "classification_basis": "支持唯一分类的简短依据",
  "rule_ids": []
}
```

同样原文出现多次时用从1开始的 `source_occurrence` 区分，每项只包含一笔交易。需计算时 `amount_calculation` 仅可包含数字、加减乘除及括号，每个数字必须来自该笔原文。金额用字符串。日期为 `YYYY-MM-DD` 或 null；相对日期需要原消息中的准确 `date_evidence`；省略日期时二者均可 null。没有三级节点时 `category_l3=null`。`rule_ids` 非空时至少一条须支持所选路径，可同时引用用途优先关系的规则。

`add_expenses` 返回各笔 `success|failed|uncertain|rejected`。`rejected` 且 `submitted=false` 才能修正后再尝试；其他结果或 `already_submitted=true` 均禁止再次提交。参数错误的一笔不应阻止同批合法消费。

`Decision.skipped` 每项为 `{"source_text":"未提交消费准确原文","source_occurrence":1,"reason":"简短具体原因"}`。已提交消费不能再列入。没有实际消费才设 `no_consumption=true`。`finish_task` 返回 `report` 和可直接展示的 `text`；结束后任务不可再写。
