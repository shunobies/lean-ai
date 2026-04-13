"""Command safety checker — classifies shell commands before execution.

Splits compound commands on shell operators and checks each segment.
Detects shell wrapper commands that can hide arbitrary operations.

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

# Commands that need user approval (substring match)
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

# Tokens that only match at the start of a command (avoids false positives
# from substring matches — e.g. "dd " inside "git add .").
_APPROVAL_START_COMMANDS = [
    "dd ",
]

# Shell wrappers that can hide arbitrary commands — always require approval
_SHELL_WRAPPERS = [
    "bash -c",
    "sh -c",
    "eval ",
    "exec ",
]

# Shell compound operators used to chain commands
_COMPOUND_OPERATORS = ("&&", "||", ";")


def _split_compound(cmd: str) -> list[str]:
    """Split a command string on shell compound operators."""
    segments = [cmd]
    for op in _COMPOUND_OPERATORS:
        new_segments: list[str] = []
        for seg in segments:
            new_segments.extend(seg.split(op))
        segments = new_segments
    return [s.strip() for s in segments if s.strip()]


def _check_single(cmd_lower: str) -> tuple[CommandRisk, str]:
    """Classify a single command segment (no compound operators)."""
    for token in _ALWAYS_BLOCK_TOKENS:
        if token in cmd_lower:
            return CommandRisk.ALWAYS_BLOCK, f"Blocked: contains '{token}'"

    for token in _APPROVAL_COMMANDS:
        if token in cmd_lower:
            return (
                CommandRisk.REQUIRES_APPROVAL,
                f"Requires approval: contains '{token.strip()}'",
            )

    for token in _APPROVAL_START_COMMANDS:
        if cmd_lower.startswith(token):
            return (
                CommandRisk.REQUIRES_APPROVAL,
                f"Requires approval: starts with '{token.strip()}'",
            )

    return CommandRisk.SAFE, ""


def check_command(command: str) -> tuple[CommandRisk, str]:
    """Classify a shell command by risk level.

    Returns (risk_level, human_readable_reason).
    """
    cmd_lower = command.lower().strip()

    # Shell wrappers can hide arbitrary operations
    for wrapper in _SHELL_WRAPPERS:
        if wrapper in cmd_lower:
            # Still check the inner content for always-block patterns
            for token in _ALWAYS_BLOCK_TOKENS:
                if token in cmd_lower:
                    return (
                        CommandRisk.ALWAYS_BLOCK,
                        f"Blocked: contains '{token}'",
                    )
            return (
                CommandRisk.REQUIRES_APPROVAL,
                f"Requires approval: uses shell wrapper '{wrapper.strip()}'",
            )

    # Split on compound operators and check each segment
    segments = _split_compound(cmd_lower)

    worst_risk = CommandRisk.SAFE
    worst_reason = ""

    for segment in segments:
        risk, reason = _check_single(segment)
        if risk == CommandRisk.ALWAYS_BLOCK:
            return risk, reason
        if risk == CommandRisk.REQUIRES_APPROVAL:
            worst_risk = risk
            worst_reason = reason

    return worst_risk, worst_reason
