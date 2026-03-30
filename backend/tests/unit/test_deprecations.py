"""Tests for version detection utilities.

Pure unit tests — no LLM, no network calls required.
"""

import json

from lean_ai.context.deprecations import (
    _categorize,
    _detect_versions,
    _extract_major_minor,
    _parse_pep508,
    _parse_requirement_line,
)

# ---------------------------------------------------------------------------
# _extract_major_minor
# ---------------------------------------------------------------------------


class TestExtractMajorMinor:
    def test_simple_version(self):
        assert _extract_major_minor("3.12") == "3.12"

    def test_three_segments(self):
        assert _extract_major_minor("4.2.1") == "4.2"

    def test_gte_prefix(self):
        assert _extract_major_minor(">=3.12") == "3.12"

    def test_caret_prefix(self):
        assert _extract_major_minor("^18.2.0") == "18.2"

    def test_tilde_arrow(self):
        assert _extract_major_minor("~> 7.1") == "7.1"

    def test_range_stops_at_comma(self):
        assert _extract_major_minor(">=3.12,<4") == "3.12"

    def test_double_equals(self):
        assert _extract_major_minor("==4.2.1") == "4.2"

    def test_single_segment(self):
        assert _extract_major_minor("17") == "17"

    def test_empty_string(self):
        assert _extract_major_minor("") == ""

    def test_no_digits(self):
        assert _extract_major_minor("latest") == ""


# ---------------------------------------------------------------------------
# _parse_pep508
# ---------------------------------------------------------------------------


class TestParsePep508:
    def test_pinned_range(self):
        name, ver = _parse_pep508("django>=4.2,<5.0")
        assert name == "django"
        assert ver == ">=4.2,<5.0"

    def test_bare_name(self):
        name, ver = _parse_pep508("requests")
        assert name == "requests"
        assert ver == ""

    def test_gte(self):
        name, ver = _parse_pep508("uvicorn>=0.20")
        assert name == "uvicorn"
        assert ver == ">=0.20"

    def test_with_extras(self):
        name, ver = _parse_pep508("uvicorn[standard]>=0.20")
        assert name == "uvicorn"
        assert "0.20" in ver


# ---------------------------------------------------------------------------
# _parse_requirement_line
# ---------------------------------------------------------------------------


class TestParseRequirementLine:
    def test_pinned(self):
        name, ver = _parse_requirement_line("django==4.2.1")
        assert name == "django"
        assert ver == "==4.2.1"

    def test_gte(self):
        name, ver = _parse_requirement_line("celery>=5.3")
        assert name == "celery"
        assert ver == ">=5.3"

    def test_bare(self):
        name, ver = _parse_requirement_line("requests")
        assert name == "requests"
        assert ver == ""


# ---------------------------------------------------------------------------
# _detect_versions — per-ecosystem via tmp_path fixtures
# ---------------------------------------------------------------------------


class TestDetectVersions:
    def test_pyproject_toml(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nrequires-python = ">=3.12"\n'
            'dependencies = ["django>=4.2", "celery>=5.3"]\n'
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "Python" in names
        assert "django" in names
        assert "celery" in names

    def test_requirements_txt_fallback(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("flask==3.0.0\nrequests>=2.31\n")
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "flask" in names
        assert "requests" in names

    def test_package_json(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({
            "dependencies": {"react": "^18.2.0", "next": "^14.0.0"},
            "engines": {"node": ">=18"},
        }))
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "Node.js" in names
        assert "react" in names
        assert "next" in names

    def test_composer_json(self, tmp_path):
        comp = tmp_path / "composer.json"
        comp.write_text(json.dumps({
            "require": {"php": "^8.4", "laravel/framework": "^12.0"},
        }))
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "PHP" in names
        assert "laravel/framework" in names

    def test_go_mod(self, tmp_path):
        gomod = tmp_path / "go.mod"
        gomod.write_text(
            "module example.com/myapp\n\ngo 1.22\n\n"
            "require (\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n"
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "Go" in names
        assert "github.com/gin-gonic/gin" in names

    def test_gemfile(self, tmp_path):
        gemfile = tmp_path / "Gemfile"
        gemfile.write_text(
            "source 'https://rubygems.org'\n"
            "ruby '3.2.0'\n"
            "gem 'rails', '~> 7.1'\n"
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "Ruby" in names
        assert "rails" in names

    def test_cargo_toml(self, tmp_path):
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text(
            '[package]\nname = "myapp"\nedition = "2021"\n\n'
            '[dependencies]\naxum = "0.7"\n'
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "Rust" in names
        assert "axum" in names

    def test_no_files(self, tmp_path):
        deps = _detect_versions(str(tmp_path))
        assert deps == []


# ---------------------------------------------------------------------------
# _categorize
# ---------------------------------------------------------------------------


class TestCategorize:
    def test_vite_is_tooling(self):
        assert _categorize("vite") == "tooling"

    def test_tailwindcss_is_tooling(self):
        assert _categorize("tailwindcss") == "tooling"

    def test_postcss_is_tooling(self):
        assert _categorize("postcss") == "tooling"

    def test_vitest_is_tooling(self):
        assert _categorize("vitest") == "tooling"

    def test_laravel_is_framework(self):
        assert _categorize("laravel/framework") == "framework"

    def test_react_is_framework(self):
        assert _categorize("react") == "framework"

    def test_lodash_is_library(self):
        assert _categorize("lodash") == "library"

    def test_ansible_is_framework(self):
        assert _categorize("ansible") == "framework"

    def test_docker_is_framework(self):
        assert _categorize("docker") == "framework"

    def test_docker_compose_is_framework(self):
        assert _categorize("docker-compose") == "framework"

    def test_terraform_is_framework(self):
        assert _categorize("terraform") == "framework"

    def test_kubernetes_is_framework(self):
        assert _categorize("kubernetes") == "framework"

    def test_helm_is_framework(self):
        assert _categorize("helm") == "framework"

    def test_pulumi_is_framework(self):
        assert _categorize("pulumi") == "framework"

    def test_packer_is_framework(self):
        assert _categorize("packer") == "framework"


# ---------------------------------------------------------------------------
# _detect_infrastructure_versions — IaC marker file detection
# ---------------------------------------------------------------------------


class TestDetectInfrastructureVersions:
    def test_ansible_via_ansible_cfg(self, tmp_path):
        (tmp_path / "ansible.cfg").write_text("[defaults]\n")
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "ansible" in names

    def test_ansible_via_playbook_yml(self, tmp_path):
        (tmp_path / "playbook.yml").write_text("---\n- hosts: all\n")
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "ansible" in names

    def test_ansible_via_roles_dir(self, tmp_path):
        (tmp_path / "roles").mkdir()
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "ansible" in names

    def test_ansible_via_requirements_yml_collections(self, tmp_path):
        (tmp_path / "requirements.yml").write_text(
            "---\ncollections:\n  - name: ansible.builtin\n"
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "ansible" in names

    def test_ansible_via_requirements_yml_roles(self, tmp_path):
        (tmp_path / "requirements.yml").write_text(
            "---\nroles:\n  - name: geerlingguy.docker\n"
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "ansible" in names

    def test_requirements_yml_without_ansible_keys(self, tmp_path):
        (tmp_path / "requirements.yml").write_text("---\nsomething: else\n")
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "ansible" not in names

    def test_docker_via_dockerfile(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "docker" in names

    def test_docker_compose_via_compose_yml(self, tmp_path):
        (tmp_path / "docker-compose.yml").write_text(
            'version: "3.8"\nservices:\n  web:\n    image: nginx\n'
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "docker-compose" in names
        compose_dep = [d for d in deps if d.name == "docker-compose"][0]
        assert compose_dep.version == "3.8"

    def test_docker_compose_modern_no_version(self, tmp_path):
        (tmp_path / "compose.yaml").write_text(
            "services:\n  web:\n    image: nginx\n"
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "docker-compose" in names
        compose_dep = [d for d in deps if d.name == "docker-compose"][0]
        assert compose_dep.version == ""

    def test_terraform_via_main_tf(self, tmp_path):
        (tmp_path / "main.tf").write_text(
            'terraform {\n  required_version = ">= 1.5.0"\n}\n'
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "terraform" in names
        tf_dep = [d for d in deps if d.name == "terraform"][0]
        assert tf_dep.version == ">= 1.5.0"

    def test_terraform_without_version(self, tmp_path):
        (tmp_path / "variables.tf").write_text(
            'variable "region" {\n  default = "us-east-1"\n}\n'
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "terraform" in names
        tf_dep = [d for d in deps if d.name == "terraform"][0]
        assert tf_dep.version == ""

    def test_kubernetes_via_k8s_dir(self, tmp_path):
        (tmp_path / "k8s").mkdir()
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "kubernetes" in names

    def test_kubernetes_via_manifest_file(self, tmp_path):
        (tmp_path / "deployment.yaml").write_text(
            "apiVersion: apps/v1\nkind: Deployment\n"
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "kubernetes" in names

    def test_no_kubernetes_for_non_k8s_yaml(self, tmp_path):
        (tmp_path / "config.yaml").write_text(
            "apiVersion: v1\nkind: SomeCustomThing\n"
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "kubernetes" not in names

    def test_helm_via_chart_yaml(self, tmp_path):
        (tmp_path / "Chart.yaml").write_text(
            "apiVersion: v2\nname: my-chart\nversion: 0.1.0\n"
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "helm" in names
        helm_dep = [d for d in deps if d.name == "helm"][0]
        assert helm_dep.version == "v2"

    def test_pulumi_via_pulumi_yaml(self, tmp_path):
        (tmp_path / "Pulumi.yaml").write_text(
            "name: my-project\nruntime: python\n"
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "pulumi" in names

    def test_packer_via_pkr_hcl(self, tmp_path):
        (tmp_path / "image.pkr.hcl").write_text(
            'source "amazon-ebs" "example" {}\n'
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "packer" in names

    def test_no_iac_in_empty_dir(self, tmp_path):
        deps = _detect_versions(str(tmp_path))
        assert deps == []

    def test_multiple_iac_tools(self, tmp_path):
        (tmp_path / "ansible.cfg").write_text("[defaults]\n")
        (tmp_path / "Dockerfile").write_text("FROM ubuntu:22.04\n")
        (tmp_path / "main.tf").write_text('provider "aws" {}\n')
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "ansible" in names
        assert "docker" in names
        assert "terraform" in names
        assert all(d.category == "framework" for d in deps)


