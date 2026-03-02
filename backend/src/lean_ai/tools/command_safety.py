"""Command safety checker — classifies shell commands before execution.

Uses string matching (no regex) to identify dangerous commands.

Three risk levels:
  SAFE              — runs without prompt
  REQUIRES_APPROVAL — user must confirm
  ALWAYS_BLOCK      — refused outright
"""

from enum import Enum


class CommandRisk(Enum):
    SAFE = "safe"
    REQUIRES_APPROVAL = "requires_approval"
    ALWAYS_BLOCK = "always_block"


# Commands that are never allowed
_ALWAYS_BLOCK_TOKENS = [
    "rm -rf /",
    "rm -fr /",
    "rm -rf ~",
    "rm -fr ~",
    "format c:",
    "format d:",
    "dd of=/dev/sd",
    "dd of=/dev/hd",
    "dd of=/dev/nvme",
    "chmod 777 /",
    "mkfs /dev/",
]

# Commands that need user approval
_APPROVAL_COMMANDS = [
    "rm ",
    "del ",
    "erase ",
    "rmdir ",
    "shred ",
    "truncate ",
    "mkfs ",
    "dropdb ",
    "drop database",
    "chmod ",
    "chown ",
    "killall ",
    "pkill ",
    "kill -9",
    "poweroff",
    "reboot",
    "shutdown",
    "git push",
    "git reset --hard",
    "git clean",
    "npm publish",
    "pip uninstall",
]


def check_command(command: str) -> tuple[CommandRisk, str]:
    """Classify a shell command by risk level.

    Returns (risk_level, human_readable_reason).
    """
    cmd_lower = command.lower().strip()

    for token in _ALWAYS_BLOCK_TOKENS:
        if token in cmd_lower:
            return CommandRisk.ALWAYS_BLOCK, f"Blocked: contains '{token}'"

    for token in _APPROVAL_COMMANDS:
        if token in cmd_lower:
            return CommandRisk.REQUIRES_APPROVAL, f"Requires approval: contains '{token.strip()}'"

    return CommandRisk.SAFE, ""
