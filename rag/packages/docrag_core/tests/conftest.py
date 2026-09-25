"""pytest 全体の前提。業務 profile は repo 外の設定なので、テストでは fixture の JSON を使う。"""
import os
from pathlib import Path

# classification など import 時に profile を読むモジュールがあるため、テストの import より前に設定する。
os.environ["DOCRAG_DOMAIN_PROFILE_FILE"] = str(Path(__file__).with_name("fixtures") / "domain_profile.json")
