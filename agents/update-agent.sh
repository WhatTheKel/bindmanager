#!/usr/bin/env bash
# Update the BindManager pull agent on this nameserver.
#
#   sudo ./update-agent.sh            # update if a newer agent is available
#   sudo ./update-agent.sh --check    # only report installed vs. available
#
# Works for the host install from the install guides (systemd timer +
# /opt/bindmanager-agent). Docker-based nameservers are updated with
# `docker compose ... up -d --build` instead (see UPDATING.md, Part 2).
#
# What it does:
#   1. finds the installed agent, its Python and config.ini from the systemd unit
#   2. gets the latest agent: `git pull` when run from a clone of the repo,
#      otherwise downloads it from GitHub (--ref picks a tag/branch)
#   3. checks the new file runs on this server's Python before touching anything
#   4. backs up the old file, installs the new one (SELinux labels restored)
#   5. dry-runs it against the app; if that fails the old agent is put back
#   6. runs the agent once now, so Manage > Nameservers shows the new version
#
# config.ini and the systemd units are never modified.
#
# Exit codes: 0 up to date / updated, 1 error (nothing changed or rolled back),
#             2 (--check only) an update is available.
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/WhatTheKel/bindmanager"
UNIT="bindmanager-agent.service"
KEEP_BACKUPS=3

ref=""            # git ref to download (default: main)
source_file=""    # use this file instead of git/download
dest=""           # installed agent path   (default: from the systemd unit)
config=""         # agent config.ini       (default: from the systemd unit)
python=""         # interpreter            (default: from the systemd unit)
check_only=0
allow_downgrade=0
verify=1
run_now=1

usage() {
    sed -n '2,/^set -euo/p' "$0" | sed -e '/^set -euo/d' -e 's/^# \{0,1\}//'
    cat <<'EOF'
Options:
  --check            report installed and available versions; change nothing
  --ref REF          download this tag or branch from GitHub (e.g. v0.2.4)
  --source FILE      install this bindmanager_agent.py instead of fetching one
  --dest PATH        installed agent   (default: taken from the systemd unit)
  --config PATH      agent config.ini  (default: taken from the systemd unit)
  --python PATH      Python to use     (default: taken from the systemd unit)
  --allow-downgrade  install even if the fetched agent is older than this one
  --no-verify        skip the dry-run check after installing
  --no-run           don't run the agent once after updating
  -h, --help         show this help
EOF
}

die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --check)     check_only=1 ;;
        --ref)       ref="${2:?--ref needs a value}"; shift ;;
        --source)    source_file="${2:?--source needs a value}"; shift ;;
        --dest)      dest="${2:?--dest needs a value}"; shift ;;
        --config)    config="${2:?--config needs a value}"; shift ;;
        --python)    python="${2:?--python needs a value}"; shift ;;
        --allow-downgrade) allow_downgrade=1 ;;
        --no-verify) verify=0 ;;
        --no-run)    run_now=0 ;;
        -h|--help)   usage; exit 0 ;;
        *)           die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

[ "$(id -u)" -eq 0 ] || [ "$check_only" -eq 1 ] || die "run as root (sudo $0)"

have_systemd=0
command -v systemctl >/dev/null 2>&1 && systemctl cat "$UNIT" >/dev/null 2>&1 && have_systemd=1

# ── 1. Where is the agent installed, and how is it run? ───────────────────────
# ExecStart=/usr/bin/python3.9 /opt/bindmanager-agent/bindmanager_agent.py --config /etc/...ini
if [ "$have_systemd" -eq 1 ]; then
    exec_line=$(systemctl cat "$UNIT" | sed -n 's/^ExecStart=//p' | tail -1)
    read -r -a exec_words <<<"$exec_line"
    [ -n "$python" ] || python="${exec_words[0]:-}"
    [ -n "$dest" ]   || dest="${exec_words[1]:-}"
    if [ -z "$config" ]; then
        for i in "${!exec_words[@]}"; do
            [ "${exec_words[$i]}" = "--config" ] && config="${exec_words[$((i + 1))]:-}"
        done
    fi
fi
dest="${dest:-/opt/bindmanager-agent/bindmanager_agent.py}"
config="${config:-/etc/bindmanager-agent/config.ini}"
if [ -z "$python" ]; then
    python=$(command -v python3.9 || command -v python3 || true)
fi
[ -n "$python" ] && [ -x "$python" ] || die "no Python found; pass --python /usr/bin/python3.9"

if [ ! -f "$dest" ]; then
    echo "No agent installed at $dest." >&2
    echo "For a Docker-based nameserver, update with:" >&2
    echo "  git pull && docker compose -f docker-compose.agent.yml up -d --build   # or docker-compose.node.yml" >&2
    echo "For a host install in another place, pass --dest PATH." >&2
    exit 1
fi

agent_version() {   # AGENT_VERSION from a file; '' for agents before 0.2.4
    sed -n "s/^AGENT_VERSION = '\([^']*\)'.*/\1/p" "$1" | head -1
}

# ── 2. Get the latest agent into a temp file next to the installed one ───────
# (same directory → same filesystem for an atomic mv, and the right SELinux
# context after restorecon)
dest_dir=$(dirname "$dest")
if [ "$check_only" -eq 1 ]; then
    new=$(mktemp)                     # --check works without root
else
    new=$(mktemp "$dest_dir/.bindmanager_agent.new.XXXXXX")
fi
trap 'rm -f "$new"' EXIT

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(dirname "$script_dir")

if [ -n "$source_file" ]; then
    [ -f "$source_file" ] || die "--source file not found: $source_file"
    info "Using $source_file"
    cat "$source_file" >"$new"
elif [ -z "$ref" ] && [ -d "$repo_dir/.git" ] && command -v git >/dev/null 2>&1; then
    if [ "$check_only" -eq 1 ]; then
        info "Checking the latest agent in $repo_dir's upstream branch"
        git -C "$repo_dir" fetch --quiet
        git -C "$repo_dir" show "@{upstream}:agents/bindmanager_agent.py" >"$new" \
            || die "could not read the agent from the upstream branch"
    else
        info "Updating the git checkout in $repo_dir"
        git -C "$repo_dir" pull --ff-only --quiet \
            || die "git pull failed (local changes in $repo_dir?). Fix that, or use --ref main to download instead."
        cat "$repo_dir/agents/bindmanager_agent.py" >"$new"
    fi
else
    url="$REPO_RAW/${ref:-main}/agents/bindmanager_agent.py"
    info "Downloading $url"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --max-time 60 "$url" -o "$new" || die "download failed: $url"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -T 60 -O "$new" "$url" || die "download failed: $url"
    else
        die "neither curl nor wget is installed; copy the file over and use --source FILE"
    fi
fi

# ── 3. Is it a working agent for this server's Python? ───────────────────────
grep -q '^def sync(' "$new" || die "the fetched file doesn't look like bindmanager_agent.py"
# (--help, not --version: agents before 0.2.4 don't have --version)
"$python" "$new" --help >/dev/null 2>&1 \
    || die "the new agent doesn't run with $python: $("$python" "$new" --help 2>&1 | tail -1)"

old_raw=$(agent_version "$dest"); new_raw=$(agent_version "$new")
old_v="${old_raw:-unknown (before 0.2.4)}"
new_v="${new_raw:-unknown (before 0.2.4)}"

echo "Installed: $old_v   ($dest)"
echo "Available: $new_v"

if cmp -s "$new" "$dest"; then
    echo "The agent is already up to date."
    exit 0
fi
# Refuse to replace a versioned agent with an older (or unversioned) one,
# e.g. when GitHub's main is behind what this server already runs.
if [ -n "$old_raw" ] && [ "$allow_downgrade" -eq 0 ] && { [ -z "$new_raw" ] ||
   [ "$(printf '%s\n%s\n' "$old_raw" "$new_raw" | sort -V | head -1)" = "$new_raw" ]; }; then
    echo "The fetched agent ($new_v) is not newer than the installed one ($old_v); nothing to do."
    echo "(Use --allow-downgrade to install it anyway.)"
    exit 0
fi
if [ "$check_only" -eq 1 ]; then
    echo "An update is available. Run without --check to install it."
    exit 2
fi

# ── 4. Back up and install ───────────────────────────────────────────────────
backup="$dest.bak-$(date +%Y%m%d-%H%M%S)"
cp -p "$dest" "$backup"
chmod 0755 "$new"
chown --reference="$dest" "$new" 2>/dev/null || true
mv -f "$new" "$dest"
trap - EXIT
command -v restorecon >/dev/null 2>&1 && restorecon -F "$dest" 2>/dev/null || true
info "Installed $new_v (previous agent saved as $backup)"

rollback() {
    echo "Restoring the previous agent from $backup" >&2
    \cp -pf "$backup" "$dest"
    command -v restorecon >/dev/null 2>&1 && restorecon -F "$dest" 2>/dev/null || true
    exit 1
}

# ── 5. Does it still reach the app with this server's config? ─────────────────
if [ "$verify" -eq 1 ]; then
    [ -r "$config" ] || { echo "Config not found: $config (pass --config)" >&2; rollback; }
    info "Checking it against the app (dry run)"
    if ! out=$("$python" "$dest" --config "$config" --dry-run 2>&1); then
        echo "$out" | tail -5 >&2
        echo "The new agent's dry run failed. If the app is down or unreachable," >&2
        echo "re-run this script once it's back (or use --no-verify)." >&2
        rollback
    fi
    echo "$out" | grep -E 'dry-run:' | tail -1 || true
fi

# Keep the newest few backups only
ls -1t "$dest".bak-* 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f || true

# ── 6. Run it once now so the app sees the new version straight away ─────────
if [ "$run_now" -eq 1 ] && [ "$have_systemd" -eq 1 ]; then
    info "Running the agent once"
    if systemctl start "$UNIT"; then
        journalctl -u "$UNIT" -n 3 --no-pager -o cat 2>/dev/null || true
    else
        echo "WARNING: the run failed; see: journalctl -u $UNIT -n 50" >&2
    fi
fi

# Units shipped with this version differ from the installed ones? Only tell.
if [ "$have_systemd" -eq 1 ] && [ -d "$repo_dir/agents/systemd" ] && [ -z "$source_file" ] && [ -z "$ref" ]; then
    for u in bindmanager-agent.service bindmanager-agent.timer; do
        shipped="$repo_dir/agents/systemd/$u"; installed="/etc/systemd/system/$u"
        [ -f "$shipped" ] && [ -f "$installed" ] || continue
        # ignore the python path, which the RHEL guide changes on purpose
        if ! diff -q <(sed 's|^ExecStart=[^ ]*|ExecStart=PY|' "$shipped") \
                     <(sed 's|^ExecStart=[^ ]*|ExecStart=PY|' "$installed") >/dev/null; then
            echo "NOTE: $installed differs from the one in this release; see UPDATING.md, Part 2.1."
        fi
    done
fi

echo "Done: $old_v -> $new_v. Manage > Nameservers in the app should show $new_v within 2 minutes."
