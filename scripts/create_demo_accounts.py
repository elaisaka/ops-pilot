"""Explicit local initialization; never print credentials or overwrite accounts."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.auth import create_demo_credentials


def main():
    directory = ROOT / "data"
    private = directory / "demo-credentials.json"
    if private.exists() or (directory / "demo-auth.json").exists():
        print("账号文件已存在，未覆盖。请使用原本地凭据。")
        return
    _, tokens = create_demo_credentials(directory)
    with private.open("x", encoding="utf-8") as stream:
        json.dump(tokens, stream, indent=2)
    private.chmod(0o600)
    print("已生成随机演示账号。凭据仅保存在 data/demo-credentials.json；请在本机编辑器查看，不要提交或分享。")


if __name__ == "__main__":
    main()
