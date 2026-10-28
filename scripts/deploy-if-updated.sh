#!/usr/bin/env bash
# 在 Linux 上由 cron 调用；完整函数先解析，避免 git 更新脚本自身时影响本次执行。
main() {
    set -Eeuo pipefail
    umask 077
    export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
    export GIT_TERMINAL_PROMPT=0
    export GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=15'

    local mode=deploy
    if [[ ${1:-} == --mark-current ]]; then
        mode=mark
        shift
    fi
    if (( $# > 1 )); then
        printf '用法: bash deploy-if-updated.sh [--mark-current] [项目目录]\n' >&2
        return 2
    fi
    local repo_dir
    repo_dir=${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}
    cd -- "$repo_dir"
    [[ -f compose.yaml && -f .env.production ]] || {
        printf '缺少 compose.yaml 或 .env.production，请检查项目目录。\n' >&2
        return 1
    }
    for required in git docker flock timeout find; do
        command -v "$required" >/dev/null || {
            printf '缺少命令: %s\n' "$required" >&2
            return 1
        }
    done

    local state_dir="$PWD/.deploy" log_file last_success target current container_id state
    mkdir -p -- "$state_dir/logs"
    exec 9>"$state_dir/deploy.lock"
    # 另一次部署尚未结束时直接退出，不等待也不启动第二个构建。
    flock -n 9 || return 0
    log_file="$state_dir/logs/deploy-$(date +%F).log"
    exec >>"$log_file" 2>&1
    find "$state_dir/logs" -maxdepth 1 -type f -name 'deploy-????-??-??.log' -mtime +14 -delete
    trap 'printf "[%s] 部署失败，退出码=%s，未更新成功标记。\n" "$(date -Is)" "$?" >&2' ERR

    printf '[%s] 开始检查，模式=%s\n' "$(date -Is)" "$mode"
    [[ $(git rev-parse --show-toplevel) == "$PWD" ]] || {
        printf '当前目录不是仓库根目录。\n' >&2
        return 1
    }
    [[ $(git branch --show-current) == main ]] || {
        printf '仅允许在 main 分支自动部署。\n' >&2
        return 1
    }
    # 忽略未跟踪文件；受 Git 管理的配置、代码和暂存内容必须保持干净。
    if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
        printf '存在已跟踪文件的本地修改，暂停部署；请先核对 git status。\n' >&2
        return 1
    fi
    export QBOT_ENV_FILE=.env.production
    local -a compose=(docker compose --env-file .env.production -p qbot-prod)
    current=$(git rev-parse HEAD)

    if [[ $mode == mark ]]; then
        # 仅供维护者确认已手动完成对应代码构建及数据库升级之后初始化/恢复基线。
        container_id=$("${compose[@]}" ps --all --quiet qbot)
        [[ -n $container_id ]] || { printf '未找到 qbot 容器，不能登记基线。\n' >&2; return 1; }
        state=$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id")
        [[ $state == 'running healthy' ]] || { printf 'qbot 尚未健康，不能登记基线。\n' >&2; return 1; }
        printf '%s\n' "$current" >"$state_dir/last-success.tmp"
        mv -- "$state_dir/last-success.tmp" "$state_dir/last-success"
        printf '[%s] 维护者确认当前版本已部署，基线=%s\n' "$(date -Is)" "$current"
        return 0
    fi

    [[ -f $state_dir/last-success ]] || {
        printf '尚无部署基线；请手动部署当前版本并健康检查后运行 --mark-current。\n' >&2
        return 1
    }
    last_success=$(cat "$state_dir/last-success")
    git cat-file -e "$last_success^{commit}"
    # 显式抓取 main，关闭交互并给网络请求总时限；失败时不碰现有容器。
    timeout 180 git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main
    target=$(git rev-parse refs/remotes/origin/main)
    git merge-base --is-ancestor "$current" "$target" || {
        printf '本地 main 与远端分叉或领先，暂停部署。\n' >&2
        return 1
    }
    git merge-base --is-ancestor "$last_success" "$target" || {
        printf '远端历史与部署基线不兼容，暂停部署。\n' >&2
        return 1
    }
    if [[ $target == "$last_success" ]]; then
        printf '[%s] 无新提交，跳过。\n' "$(date -Is)"
        return 0
    fi
    if ! git diff --quiet "$last_success" "$target" -- migrations/; then
        printf '检测到 migrations/ 变更，需手动备份、迁移和部署；完成后运行 --mark-current。\n' >&2
        return 1
    fi

    printf '[%s] 更新 %s -> %s\n' "$(date -Is)" "$last_success" "$target"
    git merge --ff-only "$target"
    # 先构建成功再替换容器；不用 --no-cache，尽量复用现有构建缓存。
    "${compose[@]}" --progress plain build qbot
    "${compose[@]}" up -d --no-build --wait --wait-timeout 120 qbot
    [[ $(git rev-parse HEAD) == "$target" ]] || {
        printf '部署期间仓库 HEAD 被外部修改，未登记成功。\n' >&2
        return 1
    }
    printf '%s\n' "$target" >"$state_dir/last-success.tmp"
    mv -- "$state_dir/last-success.tmp" "$state_dir/last-success"
    printf '[%s] 部署成功，提交=%s\n' "$(date -Is)" "$target"
}

main "$@"
