#!/bin/zsh
# Double-click from Finder after creating the project's virtual environment.
project_dir="${0:A:h}"
cd "$project_dir" || exit 1
if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
    print '请先使用 Python 3.10+ 创建 .venv，并运行：.venv/bin/python -m pip install -r requirements.txt'
    read '?按回车关闭窗口。'
    exit 1
fi
exec "$project_dir/.venv/bin/python" "$project_dir/main.py" "$@"
