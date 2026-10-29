from ..models import ToolDefinition
from .git import (
    GitBranchesArgs,
    GitBranchesData,
    GitChangedFilesData,
    GitDiffArgs,
    GitDiffData,
    GitLogArgs,
    GitLogData,
    GitShowArgs,
    GitShowData,
    GitStatusArgs,
    GitStatusData,
    git_branches,
    git_changed_files,
    git_diff,
    git_log,
    git_show,
    git_status,
)


def definitions() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            "git_status",
            GitStatusArgs,
            GitStatusData,
            git_status,
            "Inspect local staged, working and untracked Git state.",
        ),
        ToolDefinition(
            "git_diff", GitDiffArgs, GitDiffData, git_diff, "Read sanitized local Git patches."
        ),
        ToolDefinition(
            "git_log", GitLogArgs, GitLogData, git_log, "Read bounded local commit history."
        ),
        ToolDefinition(
            "git_show",
            GitShowArgs,
            GitShowData,
            git_show,
            "Read one commit, its message and sanitized patch.",
        ),
        ToolDefinition(
            "git_branches",
            GitBranchesArgs,
            GitBranchesData,
            git_branches,
            "List local branches and selected tracking metadata.",
        ),
        ToolDefinition(
            "git_changed_files",
            GitDiffArgs,
            GitChangedFilesData,
            git_changed_files,
            "List changed paths and types without patch bodies.",
        ),
    )
