#!/usr/bin/env python3
"""Render per-version Maven POMs and verify scripts from k8s/flink-runtime/<flink_version>/."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
INDEX_PATH = ROOT / "runtime-versions.json"
TEMPLATES = ROOT / "templates"
BUILD_ROOT = REPO_ROOT / "k8s" / "flink-sql-runner" / ".build"
RESERVED_DIRS = frozenset({"scripts", "templates"})


def load_index() -> dict:
    with INDEX_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def runtime_dir(runtime_key: str) -> Path:
    path = ROOT / runtime_key
    if not (path / "version.json").is_file():
        raise SystemExit(f"runtime directory not found: {path} (missing version.json)")
    return path


def discover_runtime_keys() -> list[str]:
    keys: list[str] = []
    for path in sorted(ROOT.iterdir()):
        if not path.is_dir() or path.name in RESERVED_DIRS:
            continue
        if (path / "version.json").is_file():
            keys.append(path.name)
    return keys


def load_version_entry(runtime_key: str) -> dict:
    with (runtime_dir(runtime_key) / "version.json").open(encoding="utf-8") as fh:
        entry = json.load(fh)
    flink_version = entry.get("flink_version", "")
    if flink_version and flink_version != runtime_key:
        raise SystemExit(
            f"{runtime_key}/version.json flink_version={flink_version!r} "
            f"does not match directory name {runtime_key!r}"
        )
    return entry


def resolve_runtime_key(key: str | None, index: dict) -> str:
    chosen = key or index["default"]
    known = discover_runtime_keys()
    if chosen not in known:
        raise SystemExit(f"unknown runtime key: {chosen}; known: {', '.join(known)}")
    return chosen


def render_template(name: str, mapping: dict[str, str]) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for tpl_key, value in mapping.items():
        text = text.replace(f"{{{{{tpl_key}}}}}", value)
    return text


def write_verify_connectors_sh(out_dir: Path, verify: dict) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'cd /conn',
        'test -n "$(ls target/connectors/hadoop-common-*.jar)"',
        'test -n "$(ls target/connectors/hadoop-hdfs-client-*.jar)"',
        'test -n "$(ls target/connectors/hadoop-mapreduce-client-core-*.jar)"',
        'test -n "$(ls target/connectors/commons-configuration2-*.jar)"',
        'test -n "$(ls target/connectors/woodstox-core-*.jar)"',
    ]
    seen: set[str] = set()
    for glob in verify.get("required_globs", []):
        if glob.startswith("/opt/flink/usrlib/"):
            continue
        if not (
            glob.startswith("/opt/flink/lib/")
            or glob.startswith("/opt/flink/plugins/")
        ):
            continue
        pattern = Path(glob).name
        check = f'test -n "$(ls target/connectors/{pattern})"'
        if check in seen:
            continue
        seen.add(check)
        lines.append(check)
    path = out_dir / "verify-connectors.sh"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)


def write_verify_layout_sh(out_dir: Path, verify: dict) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
    ]
    for glob in verify.get("required_globs", []):
        lines.append(f'test -n "$(ls {glob} 2>/dev/null)" || {{ echo "缺少: {glob}"; exit 1; }}')
    for name in verify.get("forbidden_files", []):
        lines.append(f'rm -f "/opt/flink/lib/{name}"')
        lines.append(f'test ! -f "/opt/flink/lib/{name}" || {{ echo "禁止存在: /opt/flink/lib/{name}"; exit 1; }}')
    if verify.get("forbidden_globs"):
        lines.append("shopt -s nullglob")
        for glob in verify["forbidden_globs"]:
            lines.extend([
                f'bad=( {glob} )',
                'if ((${#bad[@]})); then',
                f'  echo "禁止: {glob}: ${{bad[*]}}"; exit 1',
                "fi",
            ])
    path = out_dir / "verify-layout.sh"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)


def write_verify_image_spec(out_dir: Path, verify: dict, runtime_key: str) -> None:
    spec = {
        "runtime_key": runtime_key,
        "required_globs": verify.get("required_globs", []),
        "forbidden_files": verify.get("forbidden_files", []),
        "forbidden_globs": verify.get("forbidden_globs", []),
    }
    (out_dir / "verify-manifest.json").write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def render_version(runtime_key: str, index: dict) -> Path:
    entry = load_version_entry(runtime_key)
    profile = runtime_dir(runtime_key)
    out_dir = BUILD_ROOT / runtime_key
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    paimon = entry["paimon"]
    compile_paimon = entry.get("sql_runner_compile", paimon)
    mapping = {
        "RUNTIME_KEY": runtime_key,
        "FLINK_VERSION": entry["flink_version"],
        "PAIMON_ARTIFACT_ID": paimon["artifact_id"],
        "PAIMON_VERSION": paimon["version"],
        "FLINK_CDC_VERSION": entry["flink_cdc_version"],
        "SQL_RUNNER_ARTIFACT_VERSION": index["sql_runner_artifact_version"],
        "SQL_RUNNER_PAIMON_ARTIFACT_ID": compile_paimon.get("paimon_artifact_id", paimon["artifact_id"]),
        "SQL_RUNNER_PAIMON_VERSION": compile_paimon.get("paimon_version", paimon["version"]),
    }

    (out_dir / "connectors-pom.xml").write_text(
        render_template("connectors-pom.xml.tpl", mapping),
        encoding="utf-8",
    )
    (out_dir / "sql-runner-pom.xml").write_text(
        render_template("sql-runner-pom.xml.tpl", mapping),
        encoding="utf-8",
    )
    shutil.copy2(profile / "hadoop-libs.txt", out_dir / "hadoop-libs.txt")
    shutil.copy2(profile / "security-overrides.txt", out_dir / "security-overrides.txt")

    verify = entry.get("verify", {})
    write_verify_connectors_sh(out_dir, verify)
    write_verify_layout_sh(out_dir, verify)
    write_verify_image_spec(out_dir, verify, runtime_key)

    meta = {
        "runtime_key": runtime_key,
        "flink_version": entry["flink_version"],
        "base_image": entry["base_image"],
        "operator_flink_version": entry["operator_flink_version"],
        "sql_runner_jar_name": f"flink-sql-runner-{index['sql_runner_artifact_version']}.jar",
    }
    (out_dir / "build-meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"rendered {runtime_key} -> {out_dir}", file=sys.stderr)
    return out_dir


def validate_ci_matrix(index: dict) -> None:
    matrix = index.get("ci_matrix") or []
    if not matrix:
        raise SystemExit("ci_matrix is empty in runtime-versions.json")
    known = set(discover_runtime_keys())
    for key in matrix:
        if key not in known:
            raise SystemExit(
                f"ci_matrix key {key!r} missing k8s/flink-runtime/{key}/version.json"
            )
        load_version_entry(key)
    print("ci_matrix OK:", ", ".join(matrix))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="Flink base image version, e.g. 2.2.1")
    parser.add_argument("--all", action="store_true", help="render all runtime directories")
    parser.add_argument("--list", action="store_true", help="list runtime keys")
    parser.add_argument("--list-ci-matrix", action="store_true", help="print ci_matrix keys as JSON array")
    parser.add_argument("--validate-ci-matrix", action="store_true", help="verify ci_matrix dirs and version.json")
    parser.add_argument("--default", action="store_true", help="render default version only")
    parser.add_argument("--print-flink-version", metavar="KEY", help="print flink_version for KEY")
    parser.add_argument("--print-base-image", metavar="KEY", help="print base_image for KEY")
    parser.add_argument(
        "--print-sql-runner-version",
        action="store_true",
        help="print sql_runner_artifact_version from index",
    )
    args = parser.parse_args()

    index = load_index()
    keys = discover_runtime_keys()

    if args.list_ci_matrix:
        print(json.dumps(index.get("ci_matrix", [index["default"]])))
        return

    if args.validate_ci_matrix:
        validate_ci_matrix(index)
        return

    if args.print_sql_runner_version:
        print(index["sql_runner_artifact_version"])
        return

    if args.list:
        for key in keys:
            mark = " (default)" if key == index["default"] else ""
            print(f"{key}{mark}")
        return

    if args.print_flink_version:
        key = resolve_runtime_key(args.print_flink_version, index)
        print(load_version_entry(key)["flink_version"])
        return

    if args.print_base_image:
        key = resolve_runtime_key(args.print_base_image, index)
        print(load_version_entry(key)["base_image"])
        return

    if args.all:
        for key in keys:
            render_version(key, index)
        return

    if args.default:
        render_version(index["default"], index)
        return

    if args.version:
        render_version(resolve_runtime_key(args.version, index), index)
        return

    render_version(index["default"], index)


if __name__ == "__main__":
    main()
