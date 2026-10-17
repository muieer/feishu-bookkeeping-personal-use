"""Fixed local configuration. Tool payloads cannot override it."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr


def default_state_dir() -> Path:
    import sys

    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/feishu-bookkeeping"
    return Path.home() / ".local/share/feishu-bookkeeping"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    bitable_base_token: SecretStr
    bitable_table_id: str
    lark_cli_identity: Literal["user"] = "user"
    lark_cli_path: str = "lark-cli"
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    bitable_date_field: Literal["记账日期"] = "记账日期"
    bitable_amount_field: Literal["金额"] = "金额"
    bitable_description_field: Literal["具体"] = "具体"
    cli_timeout_seconds: Literal[30] = 30
    project_dir: Path

    @property
    def writable_fields(self) -> tuple[str, ...]:
        return (
            self.bitable_date_field,
            "一级分类",
            "二级分类",
            "三级分类",
            self.bitable_description_field,
            self.bitable_amount_field,
        )

    @property
    def namespace(self) -> str:
        return hashlib.sha256(
            (self.bitable_base_token.get_secret_value() + ":" + self.bitable_table_id).encode()
        ).hexdigest()


def load_settings(bundle: Path) -> Settings:
    # No environment overrides or model-controlled file/target arguments.
    data = json.loads((bundle / "private/config.json").read_text())
    return Settings(**data, project_dir=bundle / "private")
