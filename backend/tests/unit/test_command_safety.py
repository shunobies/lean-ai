"""Tests for the command safety classifier."""

from lean_ai.tools.command_safety import CommandRisk, check_command


class TestSafeCommands:
    def test_build_commands(self):
        assert check_command("npm run build")[0] == CommandRisk.SAFE
        assert check_command("cargo build --release")[0] == CommandRisk.SAFE
        assert check_command("go build ./...")[0] == CommandRisk.SAFE

    def test_install_commands(self):
        assert check_command("npm install")[0] == CommandRisk.SAFE
        assert check_command("pip install -e '.[dev]'")[0] == CommandRisk.SAFE

    def test_run_commands(self):
        assert check_command("python manage.py migrate")[0] == CommandRisk.SAFE
        assert check_command("ls -la")[0] == CommandRisk.SAFE
        assert check_command("cat README.md")[0] == CommandRisk.SAFE

    def test_git_safe_operations(self):
        assert check_command("git add .")[0] == CommandRisk.SAFE
        assert check_command("git commit -m 'fix'")[0] == CommandRisk.SAFE
        assert check_command("git status")[0] == CommandRisk.SAFE


class TestApprovalCommands:
    def test_rm_requires_approval(self):
        assert check_command("rm foo.txt")[0] == CommandRisk.REQUIRES_APPROVAL
        assert check_command("rm -r temp/")[0] == CommandRisk.REQUIRES_APPROVAL

    def test_dd_requires_approval(self):
        assert check_command("dd if=input.img of=output.img")[0] == CommandRisk.REQUIRES_APPROVAL
        risk, _ = check_command("dd bs=4M if=/dev/zero of=testfile")
        assert risk == CommandRisk.REQUIRES_APPROVAL

    def test_dd_no_false_positive_on_add(self):
        """Words containing 'dd' like 'add' should not trigger."""
        assert check_command("git add .")[0] == CommandRisk.SAFE
        assert check_command("address-lookup foo")[0] == CommandRisk.SAFE

    def test_git_push_requires_approval(self):
        assert check_command("git push origin main")[0] == CommandRisk.REQUIRES_APPROVAL

    def test_git_destructive_requires_approval(self):
        assert check_command("git reset --hard HEAD~1")[0] == CommandRisk.REQUIRES_APPROVAL
        assert check_command("git clean -fd")[0] == CommandRisk.REQUIRES_APPROVAL

    def test_chmod_requires_approval(self):
        assert check_command("chmod 644 file.txt")[0] == CommandRisk.REQUIRES_APPROVAL

    def test_publishing_requires_approval(self):
        assert check_command("npm publish")[0] == CommandRisk.REQUIRES_APPROVAL
        assert check_command("pip uninstall requests")[0] == CommandRisk.REQUIRES_APPROVAL

    def test_system_commands_require_approval(self):
        assert check_command("shutdown -h now")[0] == CommandRisk.REQUIRES_APPROVAL
        assert check_command("reboot")[0] == CommandRisk.REQUIRES_APPROVAL


class TestAlwaysBlocked:
    def test_rm_rf_root(self):
        assert check_command("rm -rf /")[0] == CommandRisk.ALWAYS_BLOCK
        assert check_command("rm -fr /")[0] == CommandRisk.ALWAYS_BLOCK
        assert check_command("rm -rf ~")[0] == CommandRisk.ALWAYS_BLOCK

    def test_dd_to_device(self):
        assert check_command("dd of=/dev/sda")[0] == CommandRisk.ALWAYS_BLOCK
        assert check_command("dd of=/dev/nvme0n1")[0] == CommandRisk.ALWAYS_BLOCK

    def test_format_disk(self):
        assert check_command("format c:")[0] == CommandRisk.ALWAYS_BLOCK

    def test_mkfs_device(self):
        assert check_command("mkfs /dev/sda1")[0] == CommandRisk.ALWAYS_BLOCK


class TestCaseInsensitive:
    def test_uppercase_blocked(self):
        assert check_command("RM -RF /")[0] == CommandRisk.ALWAYS_BLOCK

    def test_mixed_case_approval(self):
        assert check_command("Git Push origin main")[0] == CommandRisk.REQUIRES_APPROVAL
