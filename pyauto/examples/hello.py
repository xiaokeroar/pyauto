"""示例脚本：被 tasks.json 里的 python 任务调用。"""

import sys
from pathlib import Path

print(f"hello.py 运行中，Python {sys.version.split()[0]}")
print(f"参数：{sys.argv[1:] or '（无）'}")
print(f"当前目录：{Path.cwd()}")
