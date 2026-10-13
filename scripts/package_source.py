"""Package source deliverables only; never include credentials or runtime records."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    project = Path(__file__).resolve().parents[1]
    archive = project.parent / "finance-agent-project.zip"
    allowed_roots = {"src", "web", "docs", "tests", "scripts", ".vscode"}
    allowed_files = {
        "README.md",
        "AGENTS.md",
        "pyproject.toml",
        ".gitignore",
        ".env.example",
        "compose.yaml",
        "requirements-lock-windows-py313.txt",
        "data/README.md",
    }
    excluded_parts = {"__pycache__", ".pytest_cache", ".ruff_cache", "artifacts", "node_modules"}
    files = []
    for path in project.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(project)
        if relative.parts[0] not in allowed_roots and relative.as_posix() not in allowed_files:
            continue
        if excluded_parts.intersection(relative.parts) or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            continue
        files.append(path)
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        for path in sorted(files):
            output.write(path, "finance-agent-project/" + path.relative_to(project).as_posix())
    with ZipFile(archive) as output:
        assert output.testzip() is None
        names = output.namelist()
        assert "finance-agent-project/.env" not in names
        assert not any(
            part in {".git", ".venv", "runtime", "work", "artifacts", ".superpowers"}
            for name in names
            for part in Path(name).parts
        )
    print(f"Archive verified: {len(files)} files, {archive.stat().st_size} bytes, {archive}")


if __name__ == "__main__":
    main()
