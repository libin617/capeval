"""构建系统检测:根据仓库快照文件自动识别构建链并推荐安装命令。

支持:Python(pip/uv/poetry)、Node(npm/yarn/pnpm)、Java(Maven/Gradle)、
Go(go mod)、Rust(cargo)。
仅做文件级探测,不执行任何命令。
"""

from __future__ import annotations

from typing import Any

# (检测文件名/路径片段, 构建系统名, 推荐安装命令列表, 推荐验收命令示例)
BUILD_RULES: list[tuple[str, str, list[str], str]] = [
    # Python
    ("pyproject.toml", "python-poetry",
     ["pip install poetry", "poetry install"], "python -m pytest tests/"),
    ("pyproject.toml", "python-uv",
     ["pip install uv", "uv sync"], "python -m pytest tests/"),
    ("requirements.txt", "python-pip",
     ["pip install -r requirements.txt"], "python -m pytest tests/"),
    ("setup.py", "python-setuptools",
     ["pip install -e ."], "python -m pytest tests/"),
    ("Pipfile", "python-pipenv",
     ["pip install pipenv", "pipenv install --dev"], "python -m pytest tests/"),
    # Node
    ("package.json", "node-npm",
     ["npm install"], "npm test"),
    ("pnpm-lock.yaml", "node-pnpm",
     ["npm install -g pnpm", "pnpm install"], "pnpm test"),
    ("yarn.lock", "node-yarn",
     ["npm install -g yarn", "yarn install"], "yarn test"),
    # Java
    ("pom.xml", "java-maven",
     ["mvn install -DskipTests"], "mvn test"),
    ("build.gradle", "java-gradle",
     ["./gradlew build -x test"], "./gradlew test"),
    ("build.gradle.kts", "java-gradle-kts",
     ["./gradlew build -x test"], "./gradlew test"),
    # Go
    ("go.mod", "go-mod",
     ["go mod download"], "go test ./..."),
    # Rust
    ("Cargo.toml", "rust-cargo",
     ["cargo build"], "cargo test"),
    # Make
    ("Makefile", "make",
     ["make setup"], "make test"),
]


def detect_build_system(files: dict[str, str] | list[str]) -> dict[str, Any]:
    """根据仓库文件列表检测构建系统。

    返回 {"build_system": str, "install_commands": [...], "suggested_test_cmd": str, "confidence": float}
    未检测到则 build_system="unknown",install_commands=[]。
    """
    if isinstance(files, dict):
        paths = list(files.keys())
    else:
        paths = list(files)
    path_set = {p.strip().lstrip("./") for p in paths}

    # 按规则顺序匹配(优先级:锁文件 > 构建文件)
    for marker, name, install_cmds, test_cmd in BUILD_RULES:
        if marker in path_set or any(p.endswith("/" + marker) for p in path_set):
            return {
                "build_system": name,
                "install_commands": install_cmds,
                "suggested_test_cmd": test_cmd,
                "confidence": 1.0 if marker.endswith(".lock") or marker == "go.mod" else 0.8,
            }
    return {"build_system": "unknown", "install_commands": [], "suggested_test_cmd": "", "confidence": 0.0}
