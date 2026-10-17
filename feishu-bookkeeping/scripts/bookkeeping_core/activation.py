"""Conservative text gate; the host must supply the direct human instruction only."""

import re

WAKE = "记账小助手帮我记账"


def direct_text(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```|~~~[\s\S]*?~~~", "", text)
    text = re.sub(r"(?m)^\s*>.*$", "", text)
    text = re.sub(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"\n]*"|\x27[^\x27\n]*\x27|`[^`]*`', "", text)
    return text.strip()


def can_activate(instruction: str) -> bool:
    text = direct_text(instruction)
    patterns = [
        re.escape(WAKE),
        r"(?:使用|调用|启用|用)\s*\$?feishu-bookkeeping\s*(?:skill|技能)?\s*(?:来|帮我|为我)?\s*记账",
        r"^\$feishu-bookkeeping(?=\s|[，,:：]|$)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            before = text[: match.start()]
            after = text[match.end() :]
            # An invocation embedded in a request about the skill is not authorization.
            if re.search(r"不要|不需要|无需|不能|禁止|别|勿|不是|并非|不使用|不调用", before):
                continue
            if re.search(
                r"示例|例子|引用|提到|唤醒|口令|讨论|解释|介绍|安装|开发|修改|审阅|阅读|测试|验收|文档|附件|历史记录",
                before,
            ):
                continue
            if re.search(
                r"^(?:这(?:句|段|个)|是什么意思|有什么|怎么|如何)",
                after.lstrip(" \t\n，,。.:：；;!?！？"),
            ):
                continue
            if pattern.startswith("^") and re.search(
                r"安装|开发|审阅|解释|阅读|文档|示例|不.*记账", after
            ):
                continue
            return True
    return False
